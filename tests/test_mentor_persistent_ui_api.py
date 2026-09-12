"""Persistent review contracts on TestClient and an optional isolated browser."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from riji_agent.mentors.handoff import Handoff
from riji_agent.mentors.models import Account
from riji_agent.mentors.ui import build_review_router
from test_mentor_runtime import runtime  # noqa: F401


@pytest.fixture
def review(runtime):
    service, client, provider = runtime
    me = client.get("/api/mentors/v1/me").json()
    host = next(item for item in me["applications"] if item["persona_id"] == "host")
    binding = next(item for item in me["chats"] if item["application_id"] == host["id"])
    created = client.post("/api/mentors/v1/conversations", json={"binding_id": binding["id"],
        "question": "Synthetic long-term question", "personas": me["personas"],
        "run_personas": ["gentle_reviewer", "blunt_coach"], "mode": "reference"})
    assert created.status_code == 200
    identifier = created.json()["id"]
    path = "/api/mentors/v1/conversations/" + identifier
    preview = client.get(path + "/share-preview").json()
    assert client.post(path + "/commands", json={"id": "share", "kind": "share",
        "preview_hash": preview["preview_hash"]}).status_code == 200
    for _ in range(20):
        service.worker.run_one(identifier)
        service.worker.dispatcher.dispatch_one(identifier)
    view = client.get(path).json()
    assert view["conversation"]["status"] == "completed"
    result = next(row for row in view["artifacts"] if row["kind"] == "comparison")
    return service, client, provider, me, path, view, result


def test_handoff_endpoint_is_owner_authenticated_and_never_creates_a_draft(review):
    service, client, _, me, path, _, result = review
    body = {"id": "selected-result", "artifact_ids": [result["id"]]}
    headers = dict(client.headers)
    client.headers.clear()
    assert client.post(path + "/handoffs", json=body).status_code == 401
    transport = next(iter(service.transport_tokens))
    assert client.post(path + "/handoffs", json=body,
                       headers={"Authorization": "Bearer " + transport}).status_code == 401
    client.headers.update(headers)
    first = client.post(path + "/handoffs", json=body)
    assert first.status_code == 200, first.text
    second = client.post(path + "/handoffs", json=body)
    assert second.json() == first.json()
    selected = service.store.read("handoff", first.json()["handoff_id"], Handoff)
    assert not selected.draft_id and not selected.binding_id
    assert selected.principal_id == me["principal_id"]
    assert selected.artifact_ids == (result["id"],)
    assert selected.provenance.discussion_id == result["run_id"]
    assert "/接收转交 " + selected.id in first.json()["text"]
    assert selected.text not in first.json()["text"]


@pytest.mark.parametrize("selection,status", [([], 422), (["missing"], 409), (["missing"] * 6, 422)])
def test_handoff_selection_shape_and_missing_artifacts_are_rejected(review, selection, status):
    _, client, _, _, path, _, _ = review
    response = client.post(path + "/handoffs", json={"id": "invalid", "artifact_ids": selection})
    assert response.status_code == status


def test_duplicate_selection_and_conflicting_operation_do_not_create_more_handoffs(review):
    service, client, _, _, path, view, result = review
    body = {"id": "same-operation", "artifact_ids": [result["id"]]}
    assert client.post(path + "/handoffs", json=body).status_code == 200
    duplicate = client.post(path + "/handoffs", json={"id": "duplicate-selection",
        "artifact_ids": [result["id"], result["id"]]})
    assert duplicate.status_code == 409
    other = next(row for row in view["artifacts"] if row["kind"] == "opinion")
    conflict = client.post(path + "/handoffs", json={**body, "artifact_ids": [other["id"]]})
    assert conflict.status_code == 409
    assert len(service.store.list("handoff", view["conversation"]["id"], Handoff)) == 1


def test_other_owner_cannot_select_results_from_known_problem(review):
    service, client, _, _, path, _, result = review
    other = service.identity.register_principal(
        Account(platform="local", tenant="test", subject="other-person"), "other-memory-owner")
    service.user_tokens["another-synthetic-owner-token"] = other.id
    response = client.post(path + "/handoffs", json={"id": "unauthorized", "artifact_ids": [result["id"]]},
        headers={"Authorization": "Bearer another-synthetic-owner-token"})
    assert response.status_code == 409
    assert result["text"] not in response.text


def test_handoff_rejects_superseded_result_and_mixed_discussion_versions(review):
    service, client, _, _, path, view, result = review
    conversation = view["conversation"]
    newer = client.post(path + "/commands", json={"id": "new-run", "kind": "start_run",
        "expected_revision": conversation["input_revision"], "mode": "reference"})
    assert newer.status_code == 200, newer.text
    for _ in range(20):
        service.worker.run_one(conversation["id"])
        service.worker.dispatcher.dispatch_one(conversation["id"])
    view = client.get(path).json()
    latest = next(row for row in reversed(view["artifacts"]) if row["kind"] == "comparison")
    response = client.post(path + "/handoffs", json={"id": "mixed", "artifact_ids": [result["id"], latest["id"]]})
    assert response.status_code == 409 and response.json()["detail"] == "handoff_single_discussion_required"
    original = next(row for row in view["artifacts"] if row["kind"] == "user")
    assert client.post(path + "/commands", json={"id": "correct", "kind": "correct",
        "expected_revision": view["conversation"]["input_revision"], "text": "Corrected current background",
        "supersedes": [original["id"]]}).status_code == 200
    response = client.post(path + "/handoffs", json={"id": "old-result", "artifact_ids": [latest["id"]]})
    assert response.status_code == 409


def review_page():
    app = FastAPI()
    app.include_router(build_review_router())
    return TestClient(app).get("/admin/mentors")


def test_review_script_has_exact_csp_digest_and_safe_content_rendering():
    response = review_page()
    script = re.search(r"<script>(.*?)</script>", response.text, re.DOTALL).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    csp = response.headers["content-security-policy"]
    assert "script-src 'sha256-" + digest + "'" in csp
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "innerHTML" not in script and "localStorage" not in script and "sessionStorage" not in script
    ids = re.findall(r'\bid="([^"]+)"', response.text.split("<script>")[0])
    assert len(ids) == len(set(ids))


def test_review_browser_controls_use_synthetic_intercepted_responses(review, tmp_path):
    node, playwright, chrome = (os.environ.get(name) for name in
                               ("RIJI_TEST_UI_NODE", "RIJI_TEST_UI_PLAYWRIGHT", "RIJI_TEST_UI_CHROME"))
    if not all(value and Path(value).exists() for value in (node, playwright, chrome)):
        pytest.skip("Optional browser acceptance requires explicitly supplied local runtimes.")
    _, client, _, me, _, view, result = review
    page = review_page()
    payload = {"html": page.text, "headers": dict(page.headers), "me": me, "view": view,
               "conversations": client.get("/api/mentors/v1/conversations").json(),
               "result_id": result["id"], "playwright": playwright, "chrome": chrome}
    data = tmp_path / "synthetic-ui.json"
    data.write_text(json.dumps(payload))
    harness = tmp_path / "persistent-ui.cjs"
    harness.write_text(BROWSER_HARNESS)
    completed = subprocess.run([node, str(harness), str(data)], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert report["blocked_requests"] == [] and report["page_errors"] == []
    assert report["checks"] == ["deep_link", "host_default", "mode_defaults", "summary", "runs", "natural_message",
                                "correction", "new_run", "selected_save", "csp", "text_rendering",
                                "source_navigation", "group_only_controls", "group_only_guards",
                                "group_only_private_handoff", "group_only_export", "group_only_safe_commands",
                                "personal_input_restored", "group_only_delete"]


BROWSER_HARNESS = r"""
'use strict';
const fs=require('fs'),assert=require('assert');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const {chromium}=require(fixture.playwright);
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:fixture.chrome,
   args:['--disable-background-networking','--disable-sync']});
 try{
  const context=await browser.newContext({serviceWorkers:'block'});
  const page=await context.newPage(),calls=[],blocked=[],pageErrors=[],checks=[];
  page.setDefaultTimeout(5000);
  page.on('pageerror',e=>pageErrors.push(e.message));
  const identifier=fixture.view.conversation.id,base='/api/mentors/v1/conversations/'+identifier;
  const row=fixture.view.artifacts.find(x=>x.id===fixture.result_id);
  row.text='<img src="https://invalid.example/synthetic" onerror="window.syntheticXss=true"> Synthetic AI result';
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url()),path=url.pathname;
   if(url.origin!=='https://riji-ui.test'){blocked.push(request.url());return route.abort();}
   if(path==='/admin/mentors')return route.fulfill({status:200,headers:fixture.headers,body:fixture.html});
   if(!path.startsWith('/api/mentors/v1/')){blocked.push(request.url());return route.abort();}
   assert.strictEqual(request.headers().authorization,'Bearer synthetic-browser-token');
   let body=request.method()==='POST'?request.postDataJSON():null,data;
   calls.push({path,body});
   if(path==='/api/mentors/v1/me')data=fixture.me;
   else if(path==='/api/mentors/v1/connections')data={items:[]};
   else if(path==='/api/mentors/v1/conversations')data=fixture.conversations;
   else if(path===base)data=fixture.view;
   else if(path===base+'/messages')data={text:'Synthetic host received the update',conversation_id:identifier};
   else if(path===base+'/commands')data={status:'accepted',conversation_id:identifier};
   else if(path===base+'/handoffs')data={text:'Private preview command: /接收转交 synthetic-handoff',handoff_id:'synthetic-handoff'};
   else if(path===base+'/export')data=fixture.view;
   else{blocked.push(request.url());return route.abort();}
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('https://riji-ui.test/admin/mentors#discussion-'+identifier);
  await page.locator('#token').fill('synthetic-browser-token');await page.locator('#connect').click();
  await page.waitForFunction(()=>!document.getElementById('discussion').hidden,{},{timeout:5000}).catch(async error=>{
    throw Error(error.message+'; notice='+await page.locator('#notice').textContent()+'; errors='+JSON.stringify(pageErrors));
  });
  assert.strictEqual(await page.locator('#title').textContent(),fixture.view.conversation.question);
  assert.strictEqual(new URL(page.url()).hash,'#discussion-'+identifier);checks.push('deep_link');
  assert.strictEqual(await page.locator('#followup').inputValue(),'host');checks.push('host_default');
  assert.strictEqual(await page.locator('#personas input:checked').count(),1);
  await page.locator('#mode').selectOption('reference');
  assert.strictEqual(await page.locator('#personas input:checked').count(),4);
  await page.locator('#mode').selectOption('private');
  assert.strictEqual(await page.locator('#personas input:checked').count(),1);
  await page.locator('#mode').selectOption('debate');
  assert.strictEqual(await page.locator('#personas input:checked').count(),4);checks.push('mode_defaults');
  assert.ok(await page.locator('#workingSummary .summary-item').count());
  const older=fixture.view.summaries[0];
  await page.locator('#summaryVersion').selectOption(older.id);
  assert.ok(await page.locator('#workingSummary').textContent());
  assert.ok((await page.locator('#summaryStatus').textContent()).includes('背景版本 '+older.version));
  assert.ok((await page.locator('#summaryStatus').textContent()).includes('覆盖 '+older.covered_artifact_ids.length+' 条记录'));
  await page.locator('#summaryVersion').selectOption('current');
  const summaryStatus=await page.locator('#summaryStatus').textContent();
  assert.ok(summaryStatus.includes('覆盖 '+fixture.view.current_summary.covered_artifact_ids.length+' 条记录'));
  assert.match(summaryStatus,/\d{1,4}[\/-]\d{1,2}[\/-]\d{1,4}/);checks.push('summary');
  assert.strictEqual(await page.locator('#runs li').count(),fixture.view.runs.length);checks.push('runs');
  await page.locator('#supplement').fill('上周只有两小时');await page.locator('#send').click();
  await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('Synthetic host'));
  assert.strictEqual(calls.filter(x=>x.path===base+'/messages').at(-1).body.text,'上周只有两小时');
  checks.push('natural_message');
  await page.getByText('更正先前的情况',{exact:true}).click();
  const source=fixture.view.artifacts.find(x=>x.kind==='user'&&!x.superseded&&!x.unavailable);
  await page.locator('#correctionTarget').selectOption(source.id);
  await page.locator('#correctionText').fill('现在每周只能投入两小时');
  await page.locator('#correctionKind').selectOption('user_feedback');await page.locator('#correct').click();
  await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('已更正背景'));
  const correction=calls.filter(x=>x.path===base+'/commands'&&x.body.kind==='correct').at(-1).body;
  assert.deepStrictEqual(correction.supersedes,[source.id]);assert.strictEqual(correction.statement_kind,'user_feedback');
  checks.push('correction');
  await page.getByText('在这个问题下开启新一场讨论',{exact:true}).click();
  for(const checkbox of await page.locator('#runPersonas input').all())await checkbox.uncheck();
  await page.locator('#runPersonas input[value="gentle_reviewer"]').check();
  await page.locator('#runPersonas input[value="blunt_coach"]').check();
  await page.locator('#runMode').selectOption('debate');await page.locator('#runRounds').selectOption('1');
  await page.locator('#freshAnalysis').check();
  const started=page.waitForResponse(r=>new URL(r.url()).pathname===base+'/commands'&&r.request().postDataJSON().kind==='start_run');
  await page.locator('#startRun').click();await started;
  const run=calls.filter(x=>x.path===base+'/commands'&&x.body.kind==='start_run').at(-1).body;
  assert.deepStrictEqual(run.personas,['gentle_reviewer','blunt_coach']);
  assert.strictEqual(run.mode,'debate');assert.strictEqual(run.rounds,1);assert.strictEqual(run.reanalyze,true);
  checks.push('new_run');
  await page.locator('#saveResult').click();
  await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('请选择同一场次'));
  assert.strictEqual(calls.filter(x=>x.path===base+'/handoffs').length,0);
  await page.locator('.save-artifact[value="'+fixture.result_id+'"]').check();
  await page.locator('#saveResult').click();
  await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('synthetic-handoff'));
  assert.deepStrictEqual(calls.filter(x=>x.path===base+'/handoffs').at(-1).body.artifact_ids,[fixture.result_id]);
  checks.push('selected_save');
  const blockedInline=await page.evaluate(()=>{window.cspViolation=false;document.addEventListener('securitypolicyviolation',()=>window.cspViolation=true);const s=document.createElement('script');s.textContent='window.syntheticInline=true';document.body.append(s);return window.syntheticInline!==true;});
  assert.ok(blockedInline);checks.push('csp');
  assert.strictEqual(await page.locator('#artifacts img').count(),0);
  assert.strictEqual(await page.evaluate(()=>window.syntheticXss),undefined);checks.push('text_rendering');
  await page.locator('#workingSummary .source-link').first().click();
  assert.strictEqual(new URL(page.url()).hash,'#discussion-'+identifier,
    'Viewing a source must keep the problem deep link so reload can reopen the same problem');
  checks.push('source_navigation');
  fixture.view.conversation.source_scope='group_only';
  await page.locator('#history button').first().click();
  await page.locator('#sourceScopeNotice').waitFor({state:'visible'});
  assert.strictEqual(await page.locator('#sourceScopeNotice').textContent(),'仅本群内容，请回飞书群补充');
  assert.ok((await page.locator('#contextHelp').textContent()).includes('仅使用接管后收到的本群内容'));
  for(const control of await page.locator('#discussion textarea, #followup, #statementKind, #runPersonas input, #runMode, #runRounds, #freshAnalysis, #correctionTarget, #correctionKind, #replaceBackground, #send, #startRun, #correct').all())assert.ok(await control.isDisabled());
  for(const button of await page.locator('[data-command]').all()){
   const kind=await button.getAttribute('data-command');
   assert.strictEqual(await button.isDisabled(),!['stop','archive'].includes(kind));
  }
  for(const id of ['export','saveResult','delete','summaryVersion'])assert.ok(await page.locator('#'+id).isEnabled());
  checks.push('group_only_controls');
  const beforeGuard=calls.length;
  await page.evaluate(async()=>{document.getElementById('supplement').value='Synthetic blocked web input';await document.getElementById('send').onclick();});
  assert.strictEqual(await page.locator('#notice').textContent(),'仅本群内容，请回飞书群补充');
  for(const kind of ['summarize','resume','debate','continue','reanalyze','restore','start_run','correct','share']){
   const error=await page.evaluate(async kind=>{try{await command(kind);return ''}catch(error){return error.message}},kind);
   assert.strictEqual(error,'仅本群内容，请回飞书群补充');
  }
  assert.strictEqual(calls.length,beforeGuard);checks.push('group_only_guards');
  await page.locator('.save-artifact[value="'+fixture.result_id+'"]').check();
  await page.locator('#saveResult').click();
  await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('synthetic-handoff'));
  assert.deepStrictEqual(calls.filter(x=>x.path===base+'/handoffs').at(-1).body.artifact_ids,[fixture.result_id]);
  checks.push('group_only_private_handoff');
  const download=page.waitForEvent('download');await page.locator('#export').click();
  assert.strictEqual((await download).suggestedFilename(),'mentor-discussion.json');
  assert.ok(calls.some(x=>x.path===base+'/export'));checks.push('group_only_export');
  for(const kind of ['stop','archive']){
   const refreshed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/mentors/v1/conversations');
   await page.locator('[data-command="'+kind+'"]').click();
   await refreshed;
   assert.strictEqual(calls.filter(x=>x.path===base+'/commands').at(-1).body.kind,kind);
  }
  checks.push('group_only_safe_commands');
  fixture.view.conversation.source_scope='personal';
  await page.locator('#history button').first().click();
  await page.locator('#sourceScopeNotice').waitFor({state:'hidden'});
  for(const id of ['supplement','send','correctionText','correct','startRun'])assert.ok(await page.locator('#'+id).isEnabled());
  assert.ok(await page.locator('[data-command="reanalyze"]').isEnabled());
  assert.ok(!(await page.locator('#contextHelp').textContent()).includes('仅使用接管后收到的本群内容'));
  checks.push('personal_input_restored');
  fixture.view.conversation.source_scope='group_only';
  await page.locator('#history button').first().click();
  await page.locator('#sourceScopeNotice').waitFor({state:'visible'});
  page.once('dialog',dialog=>dialog.accept());await page.locator('#delete').click();
  await page.waitForFunction(()=>document.getElementById('discussion').hidden);
  assert.strictEqual(calls.filter(x=>x.path===base+'/commands').at(-1).body.kind,'delete');
  checks.push('group_only_delete');
  console.log(JSON.stringify({checks,blocked_requests:blocked,page_errors:pageErrors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
"""
