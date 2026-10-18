"""Build self-contained synthetic discussion wireframes; no runtime services."""
from pathlib import Path
from html import escape
import json

ROOT = Path(__file__).parent


def bubble(actor, text):
    return f'<div class="bubble"><small>{actor}</small><p>{text}</p></div>'


def panel(title, text):
    return f'<div class="panel"><strong>{title}</strong><p>{text}</p></div>'


def rows(*values):
    return ''.join(f'<div class="row">{value}<span>›</span></div>' for value in values)


def screen(identifier, group, title, req, state, body, actions, note=''):
    return dict(id=identifier, group=group, title=title, req=req, state=state,
                body=body, actions=actions, note=note)


roundtable = [
    screen('R01', '进入与接话', '导师', 'RT-01', '已连接', rows('圆桌 · 先和主持人聊聊', '温柔回顾者', '直率教练', '未来的我', '王阳明'), [('进入圆桌', 'R02')]),
    screen('R02', '进入与接话', '圆桌', 'RT-01 RT-02', '首次使用', bubble('主持人', '有什么想一起想清楚的事？先聊聊，需要时再请导师加入。')+'<div class="input">我想恢复每周阅读习惯……</div>', [('发送', 'R04'), ('已有历史', 'R03'), ('返回导师', 'R01')], '无命名、话题创建或背景表单。'),
    screen('R03', '进入与接话', '圆桌', 'RT-01 RT-02', '继续原聊天', bubble('我 · 上次', '先从每周两次阅读开始。')+bubble('主持人', '上次提到先尝试两次。这周实际做得怎么样？')+panel('当前背景', '计划与实际反馈分别保存 · 查看依据')+'<div class="input">补充新的进展……</div>', [('发送补充', 'R04'), ('当前背景', 'R13'), ('邀请导师', 'R05'), ('历史加载失败', 'R15')]),
    screen('R04', '进入与接话', '圆桌', 'RT-02', '主持人回应', bubble('我', '我想恢复每周阅读习惯，但晚上常常很累。')+bubble('主持人', '你更想先调整阅读时段，还是降低每次的阅读量？')+'<div class="input">继续聊……</div>', [('继续输入', 'R03'), ('邀请导师', 'R05'), ('断网', 'R12')]),
    screen('R05', '邀请与材料', '邀请导师', 'RT-03', '提案 · 确认前', rows('☑ 温柔回顾者', '☑ 直率教练', '☐ 未来的我', '☐ 王阳明')+panel('本次方式', '● 多角度参考　○ 辩论<br>辩论提案：默认 2 轮，可选 1 轮。')+panel('成员与发言者不同', '本次选 2 位发言；本桌成员仍为主持人和四位固定导师。'), [('核对材料', 'R06'), ('取消', 'R03')], '[待确认] 默认成员范围和轮数；选择发言者不会静默缩小可见范围。'),
    screen('R06', '邀请与材料', '本次讨论材料', 'RT-03 RT-04', '等待本人确认', panel('选定的当前聊天', '想恢复阅读习惯；晚上容易疲倦。<br>未包含其他导师私聊。')+panel('谁可看到', '主持人＋四位固定导师。新增日记或记忆材料须逐项核验。')+rows('查看／删改选定文字', '按需选择额外资料'), [('确认并开始', 'R07'), ('资料不允许使用', 'R14'), ('返回修改', 'R05')]),
    screen('R07', '讨论与控制', '圆桌 · 参考中', 'RT-05 RT-06', '已有部分回复', bubble('温柔回顾者', '先把开始阅读的负担降下来，例如读几页就可以结束。')+bubble('直率教练 · 正在回复', '等待这一位完成……')+panel('讨论仍在进行', '已完成 1 / 2 位 · 关闭页面不会停止讨论。')+'<div class="input">补充背景……</div>', [('补充或先总结', 'R08'), ('停止', 'R08'), ('回复完成', 'R09'), ('断线', 'R12')]),
    screen('R08', '讨论与控制', '圆桌 · 部分结果', 'RT-05 RT-06', '停止／修订状态', panel('停止已确认', '保留已经完成的发言，不再投递迟到结果。')+bubble('我 · 新补充', '下周工作时间会变化。')+panel('背景已更新', '原有意见基于上一个版本。是否重新讨论由你决定。'), [('查看已有结果', 'R09'), ('核对背景', 'R13'), ('重新邀请', 'R05')], '停止确认前显示“正在停止”；结果未知时进入 R12 核对，不显示已停止。'),
    screen('R09', '结论与继续', '圆桌 · 本次结论', 'RT-07', '完成／部分完成注明', panel('共同建议', '先安排一次容易完成的阅读，记录实际感受。')+panel('不同意见', '一位强调降低负担，一位强调固定时段。')+panel('适用条件', '尚未验证哪个时段更合适；这是建议，不代表已经执行。')+'<div class="input">继续追问主持人或点名导师……</div>', [('继续聊', 'R03'), ('选定结果保存', 'R10'), ('重新讨论', 'R05'), ('依据', 'R13')]),
    screen('R10', '保存选定结果', '保存到日记', 'RT-09', '仅预览', panel('目标日期', '示例：2030-06-12 · 可更改')+panel('AI 导师讨论结果', '选定建议文字……<br>保留讨论来源和 AI 性质。')+panel('写入预览', '只追加已选内容，不覆盖原正文；保存不表示本人已经执行。'), [('确认保存', 'R11'), ('原文发生变化', 'R16'), ('取消', 'R09')]),
    screen('R11', '保存选定结果', '已保存选定结果', 'RT-09', '本机保存成功', panel('已保存在此设备', '保留保存回执；iCloud 同步状态单独展示。')+panel('接下来', '可以继续讨论，或去日记中查看。'), [('回到圆桌', 'R09'), ('查看日记', 'R17')], '日记内保留 AI 结果性质及对应讨论来源。'),
    screen('R12', '中断与恢复', '圆桌 · 等待联网', 'RT-02 RT-06 RT-10', '离线／结果未知', panel('草稿已留在此设备', '已发送消息正在核对是否被接收，暂不重复发送。')+rows('已下载历史仍可读', '联网后核对原操作')+'<div class="input">未发送草稿……</div>', [('恢复并核对', 'R03'), ('发现仍在运行', 'R07'), ('服务失败', 'R15')]),
    screen('R13', '结论与继续', '当前背景与依据', 'RT-07 RT-08', '可追溯', panel('当前计划', '每周尝试两次阅读 · 用户原话')+panel('实际反馈', '暂未提供，不能按计划推断完成。')+rows('查看本次选定材料', '查看历史版本与当时结论'), [('纠正背景', 'R14'), ('返回聊天', 'R03')]),
    screen('R14', '邀请与材料', '材料需要更新', 'RT-04 RT-08', '来源变化／权限不足', panel('一项资料已无法用于本次讨论', '保留你的输入；移除该项或重新选择获准材料后再继续。')+panel('纠正背景', '修订本人原话后建立新版本，旧结果保留原条件。'), [('核对材料', 'R06'), ('返回聊天', 'R03')]),
    screen('R15', '中断与恢复', '圆桌暂时不可用', 'RT-01 RT-10', '服务失败／授权失效', panel('没有把历史清空', '目前无法读取服务。日记仍可使用；已有草稿保留。')+rows('重试读取', '设置 · 查看服务状态'), [('恢复读取', 'R03'), ('返回导师', 'R01')], '授权失效时不展示未经核验的旧内容；日常不出现地址或配对码表单。'),
    screen('R16', '保存选定结果', '日记保存与恢复', 'RT-09', '失败／目录权限', panel('原文发生变化时', '保留选定结果与原文，比较后重新预览，不能静默覆盖。')+panel('尚未授权目录时', '沿用系统文件夹选择；取消仍保留讨论和保存预览。'), [('重新预览', 'R10'), ('回到圆桌', 'R09')]),
    screen('R17', '保存选定结果', '日记 · 2030-06-12', 'RT-09', '打开保存目标', panel('本人记录', '既有正文保持不变，可直接在正文继续写。')+panel('AI 导师讨论结果', '选定建议文字……<br>来源：本次圆桌 · 这是建议，不代表已经执行。'), [('回到圆桌', 'R09')]),
]

memory = [
    screen('M01', '概览与分类', '记忆', 'MM-01', '主题概览 · 合成示例', panel('系统目前如何理解你', '上次整理：示例时间 · 部分条目待更新')+rows('偏好与习惯 · 喜欢先读短章节', '目标与计划 · 正在尝试规律阅读', '工作与学习', '关系与生活', '经历与变化', '其他认识', '导师观察 · 分导师查看')+panel('快捷入口', '最近变化　　待复核'), [('进入分类', 'M02'), ('导师观察', 'M13'), ('待复核', 'M09'), ('整理状态', 'M08'), ('尚未整理', 'M07'), ('离线状态', 'M10')], '首页不平铺原始记录。[待确认] 卡片或紧凑目录。'),
    screen('M02', '概览与分类', '偏好与习惯', 'MM-01 MM-02', '已整理', panel('更容易从短章节开始', '来自数条相关记忆；长期稳定性仍需后续反馈。')+panel('倾向于在安静时阅读', '历史阶段认识 · 最近尚未复核'), [('查看第一条认识', 'M03'), ('返回概览', 'M01'), ('当前没有摘要', 'M14')]),
    screen('M03', '认识与证据', '一条认识', 'MM-02 MM-03', '摘要与限制', panel('更容易从短章节开始', '这是对已保存记录的归纳，不代表所有场景都适用。')+panel('依据与限制', '合成示例中有两次记录支持；尚缺忙碌阶段的反馈。')+rows('展开依据', '这条认识不准确'), [('查看证据', 'M04'), ('纠正', 'M05'), ('回分类', 'M02')]),
    screen('M04', '认识与证据', '支撑这条认识的记录', 'MM-03 MM-04', '按需展开', panel('记忆 A · 本人记录', '读短章节更容易开始。<br>来源：示例日记 · 来源版本有效')+panel('记忆 B · 本人反馈', '另一日期的记录提供补充。<br>同一天的重复引用不算新经历。')+rows('打开原日记', '查看原始记忆详情'), [('选择记录纠正', 'M05'), ('原始记录管理', 'M12'), ('原文不可用', 'M11'), ('回摘要', 'M03')]),
    screen('M05', '纠正与复核', '纠正原始记忆', 'MM-04 MM-07', '编辑／比较', panel('选择要纠正的依据', '修改原始记忆 A；不会直接把摘要改成无来源事实。')+'<div class="input">调整后的本人事实……</div>'+panel('修改前后', '先核对差异；服务确认后才显示生效。'), [('确认纠正', 'M06'), ('远端版本已变化', 'M11'), ('取消', 'M04')]),
    screen('M06', '纠正与复核', '更改已生效', 'MM-04 MM-07', '摘要待更新', panel('原始记忆已更新', '旧摘要的依据版本已失效，暂不作为当前认识展示。')+panel('整理状态', '等待有界整理，不虚构新的概括。'), [('回概览', 'M01'), ('查看状态', 'M08')]),
    screen('M07', '整理与空状态', '还没有整理概览', 'MM-06 MM-10', '已有记忆／尚未整理', panel('目前有原始记录', '主题摘要尚未生成。可以查看原始记录，或在已授权范围内请求整理。'), [('请求整理', 'M08'), ('查看原始记录', 'M12'), ('暂不处理', 'M01')], '没有权限时指向既有资料使用设置；不默认替用户授权。'),
    screen('M08', '整理与空状态', '整理状态', 'MM-06 MM-07', '进行中／部分完成', panel('已有概览可以继续看', '示例：已覆盖 18 条／20 条输入；2 条已停止。数量为虚构。')+panel('待处理与已停止分开', '停止项不会因为点“刷新”而重开历史批次。')+rows('刷新状态', '查看哪些主题待更新'), [('看现有概览', 'M01'), ('本轮失败', 'M15'), ('来源变化', 'M11')]),
    screen('M09', '纠正与复核', '待复核', 'MM-08', '比较建议', panel('可能是状态变化', '记录 A：此前计划晚间阅读。<br>记录 B：后来尝试改到早晨。')+panel('为何需要核对', '两条有不同时间，不应仅因较新就删除旧记录。')+rows('查看双方依据', '暂时保留'), [('核对原始记录', 'M12'), ('纠正其中一条', 'M05'), ('返回概览', 'M01')]),
    screen('M10', '离线与恢复', '记忆 · 离线副本', 'MM-09 MM-10', '服务无法连接', panel('上次读取：示例时间', '可以查看仍获准的已下载内容，未提交修改保留为草稿。')+panel('当前不能确认远端变化', '不把本机草稿显示成服务已更新。'), [('联网重读', 'M01'), ('返回已下载摘要', 'M03')]),
    screen('M11', '离线与恢复', '这项依据已变化', 'MM-07 MM-09', '版本变化／撤权／来源不可用', panel('旧认识暂不展示为当前事实', '保留状态说明，重新核对合法的当前来源。')+panel('纠正冲突', '显示当前原文与本机修改，比较后再提交；不能自动覆盖。'), [('重新读取证据', 'M04'), ('重新核对修改', 'M05'), ('回概览', 'M01')]),
    screen('M12', '纠正与复核', '原始记忆', 'MM-04', '深层管理入口', '<div class="input">搜索原始记录……</div>'+rows('查看单条内容与来源', '仍然有效 · 复核', '归档／恢复', '删除此条记忆')+panel('删除范围', '只删除所选记忆，保留原日记；确认前展示对象，可取消。'), [('纠正', 'M05'), ('归档／删除确认', 'M16'), ('取消管理', 'M04')], '保留现有删除确认，不在概览卡片放危险快捷操作。'),
    screen('M13', '认识与证据', '导师观察', 'MM-02 MM-05', '按导师隔离', rows('温柔回顾者', '直率教练', '未来的我', '王阳明')+panel('示例 · 温柔回顾者的待验证观察', '某些阶段可能受开始门槛影响。<br>查看支持、反例与适用限制；不自动升级成共享事实。'), [('查看观察依据', 'M04'), ('返回概览', 'M01')], '[待确认] 独立入口还是在各分类中分导师显示。'),
    screen('M14', '整理与空状态', '暂无可见认识', 'MM-02 MM-10', '没有数据／权限不允许', panel('空状态要说明原因', '可能尚无记录，或此范围未获准显示。不能用模型猜一份画像填满页面。'), [('回概览', 'M01'), ('已有记录但未整理', 'M07')]),
    screen('M15', '整理与空状态', '这次整理未完成', 'MM-10', '失败与可恢复状态', panel('保留原始记忆和有效旧结果', '本次没有生成完整的新概览。重新请求仍受现有授权与预算限制。')+rows('查看整理状态', '继续浏览有效结果'), [('核对状态', 'M08'), ('返回概览', 'M01')]),
    screen('M16', '纠正与复核', '确认更改这条记忆', 'MM-04 MM-07', '确认前／可取消', panel('所选对象', '记忆 A · 本人记录<br>内容与来源摘要……')+panel('操作范围', '归档后保留记录，可恢复；删除只影响这条记忆，不删除原日记。关联摘要将重新核对。'), [('确认所选操作', 'M06'), ('取消', 'M12'), ('版本变化或操作失败', 'M11')], '真实界面按已选操作显示“归档”或“删除”；不把两个操作合为一次提交。'),
]

CSS = '''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:#202020;background:#f4f4f4;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:inherit;text-underline-offset:3px}header,main,footer{max-width:1200px;margin:auto;padding:28px}header{border-bottom:2px solid #222}h1{font-size:30px;line-height:1.25;margin:10px 0}h2{font-size:23px;margin:32px 0 16px}h3{font-size:17px;margin:8px 0}p{margin:8px 0}small,.meta{font-size:12px;color:#595959}.badge{display:inline-block;padding:3px 9px;border:1px solid #666;border-radius:4px;font-size:12px}.intro{max-width:760px}.index{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0}.index a,.tool{border:1px solid #777;background:white;padding:8px 12px;border-radius:5px;font:inherit;cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,315px),1fr));gap:24px;align-items:start}.card{min-width:0;scroll-margin-top:18px}.phone{border:2px solid #333;border-radius:22px;background:#fff;overflow:hidden;margin:10px 0}.statusbar{padding:8px 18px;display:flex;justify-content:space-between;font-size:11px;border-bottom:1px solid #ddd}.titlebar{padding:16px;border-bottom:1px solid #ccc;font-weight:700;text-align:center}.content{padding:16px;min-height:370px}.bubble{margin:12px 0;padding:11px;background:#f0f0f0;border-radius:7px}.bubble:nth-child(even){margin-left:20px;border:1px solid #bbb;background:#fff}.panel{border:1px solid #b9b9b9;padding:12px;margin:12px 0;border-radius:5px}.panel p{font-size:14px}.input{padding:12px;border:1px dashed #777;color:#666;margin:14px 0}.row{padding:13px 0;border-bottom:1px solid #ddd;display:flex;justify-content:space-between;gap:8px}.row span{padding-left:8px}.tabs{display:flex;justify-content:space-around;font-size:12px;border-top:1px solid #aaa;padding:14px 4px;background:#eee}.tabs b{border-bottom:2px solid #333}.actions{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.actions a{border:1px solid #666;border-radius:4px;background:white;padding:6px 9px;font-size:12px}.note{font-size:12px;border-left:2px solid #888;padding-left:9px}.feedback{display:block;margin-top:12px;font-size:13px}.feedback textarea{display:block;resize:vertical;width:100%;min-height:64px;border:1px solid #aaa;border-radius:4px;padding:8px;font:inherit;background:#fafafa}table{width:100%;border-collapse:collapse;background:#fff}td,th{padding:10px;border:1px solid #ccc;text-align:left;vertical-align:top}td:first-child{white-space:nowrap}.coverage{overflow:auto}button:focus-visible,a:focus-visible,textarea:focus-visible{outline:3px solid #444;outline-offset:3px}footer{border-top:2px solid #222}@media(max-width:480px){header,main,footer{padding:18px}h1{font-size:25px}.grid{gap:24px}.content{min-height:0}.tool{width:100%;margin:4px 0}}@media print{.feedback,.tools{display:none}.card{break-inside:avoid}body{background:#fff}}'''

JS = '''const KEY=STORAGE_KEY;const fields=[...document.querySelectorAll('textarea[data-screen]')];const status=document.getElementById('save-status');let records={};try{records=JSON.parse(localStorage.getItem(KEY)||'{}');if(!records||typeof records!=='object'||Array.isArray(records))records={};}catch(e){status.textContent='浏览器本地保存不可用，请及时导出意见。';}for(const f of fields){f.value=typeof records[f.dataset.screen]==='string'?records[f.dataset.screen]:'';f.addEventListener('input',()=>{records[f.dataset.screen]=f.value;try{localStorage.setItem(KEY,JSON.stringify(records));status.textContent='意见已保存在当前浏览器；请导出后交给 Codex。';}catch(e){status.textContent='未能保存到浏览器，请立即导出。';}});}function exportNotes(format){const payload={prototype:document.title,version:'0.1',exportedAt:new Date().toISOString(),feedback:fields.map(f=>({screen:f.dataset.screen,text:f.value})).filter(x=>x.text.trim())};const content=format==='json'?JSON.stringify(payload,null,2):'# '+payload.prototype+' — 反馈\\n\\n'+payload.feedback.map(x=>'## '+x.screen+'\\n\\n'+x.text).join('\\n\\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([content],{type:format==='json'?'application/json':'text/markdown;charset=utf-8'}));a.download=KEY+'-feedback.'+(format==='json'?'json':'md');a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}document.getElementById('export-json').onclick=()=>exportNotes('json');document.getElementById('export-md').onclick=()=>exportNotes('md');'''


def build(name, title, prd, active, data, decisions):
    groups = list(dict.fromkeys(item['group'] for item in data))
    ids = {item['id'] for item in data}
    assert len(ids) == len(data)
    assert all(target in ids for item in data for _, target in item['actions'])
    intro = f'''<header><span class="badge">讨论稿 v0.1 · 2026-09-18 · 非运行截图</span><h1>{title}</h1><p class="intro">黑白低保真全流程。全部示例为虚构数据；按钮只跳转原型，不调用 AI、不保存日记、不改变真实记忆。请先看主要体验，再看异常与恢复。</p><p><a href="../{prd}">对应 PRD</a> · <a href="index.html">两项设计总览</a></p><nav class="index">'''
    intro += ''.join(f'<a href="#group-{i}">{i+1}. {group}</a>' for i, group in enumerate(groups))
    intro += '<a href="#coverage">需求覆盖</a><a href="#decisions">待讨论</a></nav><div class="tools"><button class="tool" id="export-json">导出 JSON 意见</button> <button class="tool" id="export-md">导出 Markdown 意见</button><p class="meta" id="save-status">逐屏意见仅保存在当前浏览器，不会自动发送；导出后交给 Codex。</p></div></header><main>'
    sections = []
    coverage = {}
    for i, group in enumerate(groups):
        cards = []
        for item in (item for item in data if item['group'] == group):
            for req in item['req'].split(): coverage.setdefault(req, []).append(item['id'])
            tabs = ''.join(f'<{"b" if label == active else "span"}>{label}</{"b" if label == active else "span"}>' for label in ('日记','导师','记忆','设置'))
            actions = ''.join(f'<a href="#{target}">{label} → {target}</a>' for label, target in item['actions'])
            cards.append(f'''<article class="card" id="{item['id']}"><h3>{item['id']} · {item['title']}</h3><p class="meta">来源：{item['req']} · 状态：{item['state']}</p><div class="phone"><div class="statusbar"><span>9:41</span><span>◌ ▰</span></div><div class="titlebar">〈　{item['title']}　···</div><div class="content">{item['body']}</div><div class="tabs">{tabs}</div></div><p class="meta">原型操作及目标</p><div class="actions">{actions}</div>{'<p class="note">'+item['note']+'</p>' if item['note'] else ''}<label class="feedback">对此屏的修改意见<textarea data-screen="{item['id']}" aria-label="{item['id']} 修改意见" placeholder="例如：这里的信息太多／这个入口不需要……"></textarea></label></article>''')
        sections.append(f'<section id="group-{i}"><h2>{i+1}. {group}</h2><div class="grid">'+''.join(cards)+'</div></section>')
    table = ''.join(f'<tr><td>{req}</td><td>'+', '.join(f'<a href="#{identifier}">{identifier}</a>' for identifier in members)+'</td></tr>' for req, members in sorted(coverage.items()))
    end = '<section id="coverage"><h2>需求覆盖</h2><div class="coverage"><table><tr><th>PRD 要求</th><th>页面与关键状态</th></tr>'+table+'</table></div></section><section id="decisions"><h2>待讨论的产品决定</h2><ol>'+''.join('<li>'+item+'</li>' for item in decisions)+'</ol><p>无新增系统权限弹窗。原型中的失败恢复为 PRD 契约；尚未证明真实服务已实现。任何未定提案均以评审后的 PRD 为准。</p><label class="feedback">整体意见<textarea data-screen="overall" aria-label="整体修改意见"></textarea></label></section></main><footer>Riji · 个人 MVP 产品评审 · 本地离线文档 · 日记／导师／记忆／设置</footer>'
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+' · v0.1</title><style>'+CSS+'</style>'+intro+''.join(sections)+end+'<script>'+JS.replace('STORAGE_KEY', json.dumps('riji-'+name+'-2026-09-18-v01'))+'</script></html>'
    (ROOT/('wireframe-'+name+'.html')).write_text(html)
    (ROOT/(name+'-flow-map.json')).write_text(json.dumps([{k:v for k,v in item.items() if k!='body'} for item in data],ensure_ascii=False,indent=2)+'\n')
    return {'screens':len(data),'requirements':len(coverage),'actions':sum(len(s['actions']) for s in data)}


if __name__ == '__main__':
    results = {
        'roundtable':build('roundtable','圆桌：直接开始，按需邀请','PRD-ios-roundtable.md','导师',roundtable,[
            '圆桌入口是否放在四导师上方？首次直接给主持人输入。',
            '固定全部成员可见、本次按需发言，是否符合预期？仅让所选导师可见需要另一套授权设计。',
            '新讨论放聊天菜单，旧记录放历史；不恢复启动前的话题表单。',
            '参考／辩论与默认轮数是否合适？当前草案辩论默认 2 轮。']),
        'memory':build('memory','记忆：先看主题，再看依据','PRD-ios-memory-overview.md','记忆',memory,[
            '主题卡片还是紧凑分类目录？本稿展示主题和一句代表性摘要。',
            '导师观察单独入口，还是在各主题中分导师显示？',
            '最近变化与待复核作为轻量入口，是否需要放首页？',
            '首版先做分类与证据，图谱以后独立讨论。'])}
    (ROOT/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Riji · 两项功能设计评审</title><style>'+CSS+'</style><header><span class="badge">讨论稿 · 尚未开发</span><h1>先确认体验，再做功能</h1><p>两份独立 PRD、33 个页面与状态。示例均为虚构，逐屏意见可保存在本浏览器并导出。</p></header><main><div class="grid"><section class="panel"><h2>01 圆桌对话</h2><p>直接与主持人聊，按需邀请导师，保留讨论与继续反馈。</p><p><a href="wireframe-roundtable.html">打开 17 屏全流程示意图 →</a></p><p><a href="../PRD-ios-roundtable.md">阅读独立 PRD</a> · Issue #56</p></section><section class="panel"><h2>02 结构化记忆</h2><p>默认看主题摘要，需要时再查看具体记录与来源。</p><p><a href="wireframe-memory.html">打开 16 屏全流程示意图 →</a></p><p><a href="../PRD-ios-memory-overview.md">阅读独立 PRD</a> · Issue #57</p></section></div><h2>本轮讨论顺序</h2><p>先看入口和主要流程，再看材料范围、纠正与异常恢复。意见写在对应页面下方，导出 JSON 或 Markdown 后交给 Codex；不会自动上传。</p><p>双向日期滑动和自动化测试是独立的直接开发任务，不等待这两份草案确认。</p></main></html>')
    print(json.dumps(results))
