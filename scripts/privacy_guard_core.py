#!/usr/bin/env python3
"""Reusable, stdlib-only privacy scanner. It never emits matched values."""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
import fnmatch
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

MAX_BYTES = 10 * 1024 * 1024
DEFAULT_CONFIG = Path.home() / '.config/privacy-publish-guard/config.json'
PRIVATE_GLOBS = ('.env', '.env.*', '**/.env', '**/.env.*', '*.sqlite', '*.sqlite3', '*.sqlite*', '*.db',
    '**/*.sqlite*', '**/*.db', '*.log', '**/*.log', 'auth.json', '**/auth.json',
    'id_rsa', 'id_ed25519', '**/id_rsa', '**/id_ed25519', '*.pem', '*.p12', '*.pfx',
    '**/*.pem', '**/*.p12', '**/*.pfx', 'secrets.json', '**/secrets.json',
    '**/.ssh/*', '**/sessions/*.jsonl', '**/archived_sessions/*.jsonl', 'MEMORY.md', '**/MEMORY.md')
EXAMPLE_NAMES = ('.env.example', '.env.sample', '.env.template', '.env.test.example')
EXAMPLE_WORDS = frozenset({'example','sample','placeholder','redacted','dummy','test','fake','changeme',
    'change-me','your-api-key','your_api_key','your-secret','your_secret','secret-key','test-key',
    'fake-secret','test-secret','secret-that-must-not-leak','replace-me','cli-secret','sk-openai','wiring-shared-secret','smoke-shared-secret','another-secret','top-secret-shared','tenant-token','shared-secret','not-a-real-key','not-a-real-token','example-token','token-placeholder'})
EMAIL = re.compile(r'(?<![\w.+-])[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w.-])')
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
IPV6 = re.compile(r'(?<![\w:])(?:[A-Fa-f0-9]{0,4}:){2,}[A-Fa-f0-9:.]{0,15}(?![\w:])')
PERSONAL_PATH = re.compile(r'(?i)(?:/Users/|/home/|[A-Z]:[\\/]Users[\\/])([\w.-]+|<[^>]+>)(?:[/\\][^\s"\'`<>]*)?')
TS_HOST = re.compile(r'(?i)(?<![\w.-])(?:[a-z0-9-]+\.)+[a-z0-9-]*ts\.net\b')
KEYS = (
    ('private_key',re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----')),
    ('github_token',re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b')),
    ('api_token',re.compile(r'\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}\b')),
    ('slack_token',re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{16,}\b')),
    ('aws_access_key',re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('jwt_token',re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b')),
)


QUOTED_ASSIGNMENT = re.compile(r"""(?im)(?<![\w])(?:[A-Z][A-Z0-9_]*_)?(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD|TOKEN|JWT_SECRET)\s*["']?\s*[:=]\s*(["'])([^\r\n]*?)\1""")
ENV_ASSIGNMENT = re.compile(r"(?m)^[ \t]*(?:export[ \t]+)?(?:[A-Z][A-Z0-9_]*_)?(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD|TOKEN|JWT_SECRET)[ \t]*=[ \t]*([^\s#'\"]+)[ \t]*$")


@dataclass(frozen=True)
class Finding:
    category: str
    source: str
    line: int
    column: int

class GuardError(RuntimeError):
    """Only fixed, non-sensitive error identifiers may be surfaced."""


def load_config(path: Path | None = None, *, required: bool = False) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_CONFIG
    if not target.exists():
        if required or path is not None: raise GuardError('private_config_missing')
        return {'version':1, 'private_tokens':[], 'allow_values':[], 'private_path_globs':[]}
    if target.is_symlink() or stat.S_IMODE(target.stat().st_mode) & 0o077:
        raise GuardError('private_config_permissions')
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError): raise GuardError('private_config_invalid') from None
    if not isinstance(data,dict) or data.get('version') != 1:
        raise GuardError('private_config_invalid')
    if set(data) - {'version','private_tokens','allow_values','private_path_globs'}:
        raise GuardError('private_config_invalid')
    for key in ('private_tokens','allow_values','private_path_globs'):
        data.setdefault(key,[])
        if not isinstance(data[key],list) or any(not isinstance(x,str) or not x for x in data[key]):
            raise GuardError('private_config_invalid')
    if any(len(x)<3 for x in data['private_tokens']): raise GuardError('private_token_too_short')
    return data


def placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return (lowered in EXAMPLE_WORDS or bool(re.fullmatch(r'(?:fake|test|example|sample|dummy|placeholder|redacted)[-_][a-z0-9_-]+', lowered)) or bool(re.fullmatch(r'<[a-z0-9_-]+>|\$\{[A-Za-z_][A-Za-z0-9_]*\}',value)))


def allowed(value: str, config: dict[str, Any]) -> bool:
    return value in config.get('allow_values',[]) or placeholder(value)


def safe_source(source: str, config: dict[str, Any]) -> str:
    if (Path(source).is_absolute() or EMAIL.search(source) or IPV4.search(source) or TS_HOST.search(source)
        or any(x.casefold() in source.casefold() for x in config.get('private_tokens',[]))):
        return 'source#' + hashlib.sha256(source.encode()).hexdigest()[:12]
    return source[:200]


def _finding(category: str, text: str, offset: int, source: str, config: dict[str,Any]) -> Finding:
    return Finding(category,safe_source(source,config),text.count('\n',0,offset)+1,
                   offset-text.rfind('\n',0,offset))



def _container_accounts(text: str) -> set[str]:
    """Recognize literal system accounts in Docker build instructions only."""
    if not re.search(r'(?m)^\s*FROM\s+\S+',text):return set()
    declared=set(re.findall(
        r'(?m)^\s*(?:RUN\s+|&&\s+)(?:[^#\r\n]*?&&\s+)?adduser\s+--system'
        r'(?:\s+--(?:uid|gid)\s+\d+)*\s+([\w.-]+)(?:\s*\\)?\s*$',text))
    selected=set(re.findall(r'(?m)^\s*USER\s+([\w.-]+)\s*$',text))
    return declared & selected


def _container_path(match, text: str, accounts: set[str]) -> bool:
    if not match.group().startswith('/home/') or match.group(1) not in accounts:return False
    start=text.rfind('\n',0,match.start())+1
    line=text[start:text.find('\n',match.start()) if '\n' in text[match.start():] else len(text)]
    return bool(re.match(r'^\s*(?:COPY|RUN|ENTRYPOINT|CMD|WORKDIR|ENV)\b',line))


def scan_text(text: str, source: str = 'stdin', config: dict[str,Any] | None = None) -> list[Finding]:
    config = config or {'private_tokens':[], 'allow_values':[]}
    found=[]
    def add(category: str, match, value: str | None = None):
        if not allowed(value or match.group(),config):
            found.append(_finding(category,text,match.start(),source,config))
    for item in config.get('private_tokens',[]):
        for match in re.finditer(re.escape(item),text,re.I):found.append(_finding('personal_identifier',text,match.start(),source,config))
    container_accounts=_container_accounts(text)
    for match in PERSONAL_PATH.finditer(text):
        if _container_path(match,text,container_accounts):continue
        user=match.group(1)
        if not placeholder(user) and user.lower() not in {'user','username','yourname','your-user','runner','alice','bob','test-user','example-user'}:
            add('personal_path',match)
    for match in EMAIL.finditer(text):
        domain=match.group().rsplit('@',1)[1].lower()
        if (match.group().lower() not in {'git@github.com','git@gitlab.com','git@bitbucket.org'} and domain not in {'example.com','example.org','example.net'} and not domain.endswith(('.invalid','.test','.example'))):
            add('email_address',match)
    for match in TS_HOST.finditer(text):add('tailscale_hostname',match)
    for pattern in (IPV4,IPV6):
        for match in pattern.finditer(text):
            try:ip=ipaddress.ip_address(match.group())
            except ValueError:continue
            docs=(ip.version==4 and any(ip in ipaddress.ip_network(x) for x in ('192.0.2.0/24','198.51.100.0/24','203.0.113.0/24')))
            docs=docs or (ip.version==6 and ip in ipaddress.ip_network('2001:db8::/32'))
            if not (ip.is_loopback or ip.is_unspecified or docs):add('non_example_ip',match)
    for category,pattern in KEYS:
        for match in pattern.finditer(text):add(category,match)
    for match in QUOTED_ASSIGNMENT.finditer(text):
        value=match.group(2)
        if len(value)>=8 and not placeholder(value):add('credential_assignment',match,value)
    for match in ENV_ASSIGNMENT.finditer(text):
        value=match.group(1)
        # Unquoted Python names/calls/type annotations are not credentials.
        # Prefix-based token rules above catch known key formats separately.
        if (len(value)>=20 and re.fullmatch(r'[A-Za-z0-9_+/=-]+',value)
            and re.search(r'[0-9]',value) and re.search(r'[A-Za-z]',value)
            and not placeholder(value)):
            add('credential_assignment',match,value)
    return sorted(set(found),key=lambda x:(x.source,x.line,x.column,x.category))


def scan_path_name(path: str, source: str = 'path', config: dict[str,Any] | None = None) -> list[Finding]:
    config = config or {}
    normalized=path.replace('\\','/').lstrip('./')
    # lstrip above normalizes relative prefixes, but preserve a dotfile basename.
    basename=Path(path).name
    candidate=path.replace('\\','/')
    examples=basename in EXAMPLE_NAMES or basename.endswith(('.example','.sample','.template'))
    patterns=PRIVATE_GLOBS+tuple(config.get('private_path_globs',[]))
    result=scan_text(candidate,source,config)
    if not examples and any(fnmatch.fnmatchcase(candidate,p) or fnmatch.fnmatchcase(basename,p) for p in patterns):
        result.append(Finding('private_file',safe_source(source,config),1,1))
    return result


def scan_bytes(raw: bytes, source: str = 'stdin', config: dict[str,Any] | None = None) -> list[Finding]:
    config = config or {}
    if len(raw)>MAX_BYTES:raise GuardError('scan_input_too_large')
    # Raster pixel/compression data is not text. OCR/EXIF and attachment review
    # are outside this deterministic text guard's coverage.
    image = (raw.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a'))
        or (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'))
    if image:return []
    try:text=raw.decode('utf-8')
    except UnicodeDecodeError:return [Finding('binary_review_required',safe_source(source,config),1,1)]
    if '\0' in text:return [Finding('binary_review_required',safe_source(source,config),1,1)]
    return scan_text(text,source,config)


def scan_file(path: Path, source: str, config: dict[str,Any], *, check_name: bool = True) -> list[Finding]:
    if not path.is_file() or path.is_symlink(): raise GuardError('scan_file_unavailable')
    if path.stat().st_size>MAX_BYTES: raise GuardError('scan_input_too_large')
    raw=path.read_bytes()
    result=scan_path_name(path.name,source,config) if check_name else []
    result.extend(scan_bytes(raw,source,config))
    return result


def report(findings: list[Finding], error: str | None = None) -> int:
    print(json.dumps({'version':1,'ok':not findings and error is None,'findings':[asdict(x) for x in findings],**({'error':error} if error else {})},ensure_ascii=True))
    return 2 if findings or error else 0


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['scan'])
    parser.add_argument('--config',type=Path)
    parser.add_argument('--file',type=Path,action='append',default=[])
    parser.add_argument('--stdin',action='store_true')
    args=parser.parse_args(argv)
    try:
        config=load_config(args.config)
        if not args.file and not args.stdin:raise GuardError('scan_input_required')
        found=[]
        for index,path in enumerate(args.file):found.extend(scan_file(path,f'file#{index+1}',config))
        if args.stdin:
            raw=sys.stdin.buffer.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise GuardError('scan_input_too_large')
            found.extend(scan_bytes(raw,'stdin',config))
        return report(found)
    except GuardError as exc:return report([],str(exc))
    except Exception:return report([],'scan_failed')

if __name__=='__main__':raise SystemExit(main())
