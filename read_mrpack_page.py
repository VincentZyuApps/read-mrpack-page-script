#!/usr/bin/env python3
"""Single-file MRPACK inspector web server, built entirely with the Python standard library."""

from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
import threading
import urllib.parse
import zipfile
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 60907
DEFAULT_MAX_UPLOAD_MIB = 1024
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100_000


class PackError(ValueError):
    """A safe, user-facing MRPACK parsing error."""


def human_size(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def environment(entry: dict[str, Any], side: str) -> str:
    return str(entry.get("env", {}).get(side, "required"))


def side_group(entry: dict[str, Any]) -> str:
    client = environment(entry, "client") != "unsupported"
    server = environment(entry, "server") != "unsupported"
    if client and server:
        return "both"
    if client:
        return "client_only"
    if server:
        return "server_only"
    return "unsupported"


def parse_mrpack(path: Path, source: str) -> dict[str, Any]:
    if path.suffix.casefold() != ".mrpack":
        raise PackError("Only .mrpack files are allowed.")
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > MAX_ARCHIVE_ENTRIES:
                raise PackError("The archive has too many entries to inspect safely.")
            try:
                index_info = archive.getinfo("modrinth.index.json")
            except KeyError as error:
                raise PackError("The archive does not contain modrinth.index.json.") from error
            if index_info.file_size > MAX_INDEX_BYTES:
                raise PackError("modrinth.index.json exceeds the inspection safety limit.")
            try:
                index = json.loads(archive.read(index_info).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise PackError("modrinth.index.json is not valid UTF-8 JSON.") from error
            if not isinstance(index, dict) or not isinstance(index.get("files", []), list):
                raise PackError("modrinth.index.json has an unsupported structure.")
            entries = archive.infolist()
    except PackError:
        raise
    except zipfile.BadZipFile as error:
        raise PackError("The selected file is not a valid ZIP/MRPACK archive.") from error
    except OSError as error:
        raise PackError(f"Unable to read the archive: {error}") from error

    files: list[dict[str, Any]] = []
    groups: Counter[str] = Counter()
    for entry in index["files"]:
        if not isinstance(entry, dict):
            continue
        client = environment(entry, "client")
        server = environment(entry, "server")
        group = side_group(entry)
        groups[group] += 1
        downloads = entry.get("downloads") if isinstance(entry.get("downloads"), list) else []
        hashes = entry.get("hashes") if isinstance(entry.get("hashes"), dict) else {}
        files.append(
            {
                "path": str(entry.get("path", "")),
                "client": client,
                "server": server,
                "group": group,
                "sha512": str(hashes.get("sha512", "")),
                "download": str(downloads[0]) if downloads else "",
            }
        )

    roots: Counter[str] = Counter()
    root_sizes: Counter[str] = Counter()
    for info in entries:
        root = info.filename.split("/", 1)[0] or "(root)"
        roots[root] += 1
        root_sizes[root] += info.file_size

    dependencies = index.get("dependencies") if isinstance(index.get("dependencies"), dict) else {}
    return {
        "source": {"name": path.name, "size": path.stat().st_size, "size_label": human_size(path.stat().st_size), "kind": source},
        "metadata": {
            "format_version": index.get("formatVersion", "?"),
            "game": index.get("game", "?"),
            "name": index.get("name", ""),
            "version_id": index.get("versionId", ""),
        },
        "dependencies": [{"name": str(name), "version": str(version)} for name, version in sorted(dependencies.items())],
        "stats": {
            "indexed_files": len(files),
            "both": groups["both"],
            "client_only": groups["client_only"],
            "server_only": groups["server_only"],
            "archive_entries": len(entries),
        },
        "files": files,
        "archive_paths": sorted(info.filename for info in entries if info.filename),
        "archive_roots": [
            {"name": root, "entries": count, "size": root_sizes[root], "size_label": human_size(root_sizes[root])}
            for root, count in roots.most_common()
        ],
    }


class ServerState:
    def __init__(self, max_upload_bytes: int, default_pack: Path | None) -> None:
        self.max_upload_bytes = max_upload_bytes
        self.max_upload_mib = max_upload_bytes // (1024 * 1024)
        self.current: dict[str, Any] | None = None
        self.current_error: str | None = None
        self.lock = threading.Lock()
        if default_pack:
            try:
                self.current = parse_mrpack(default_pack, "server")
            except PackError as error:
                self.current_error = str(error)

    def current_payload(self) -> dict[str, Any]:
        with self.lock:
            return {"pack": self.current, "error": self.current_error, "max_upload_mib": self.max_upload_mib}


class MrpackHandler(BaseHTTPRequestHandler):
    server: "MrpackServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {format % args}")

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def send_page(self) -> None:
        encoded = PAGE.replace("__MAX_UPLOAD_MIB__", str(self.server.state.max_upload_mib)).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            self.send_page()
        elif path == "/api/current":
            self.send_json(self.server.state.current_payload())
        else:
            self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path != "/api/inspect":
            self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        length_header = self.headers.get("Content-Length")
        if not length_header or not length_header.isdigit():
            self.send_json({"error": "A valid Content-Length header is required."}, HTTPStatus.LENGTH_REQUIRED)
            return
        content_length = int(length_header)
        if content_length <= 0:
            self.send_json({"error": "Choose a non-empty .mrpack file."}, HTTPStatus.BAD_REQUEST)
            return
        if content_length > self.server.state.max_upload_bytes:
            self.send_json(
                {"error": f"Upload exceeds the {self.server.state.max_upload_mib} MiB limit."},
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return
        filename = urllib.parse.unquote(self.headers.get("X-MRPACK-Filename", "upload.mrpack"))
        filename = Path(filename).name
        if not filename.casefold().endswith(".mrpack"):
            self.send_json({"error": "Only .mrpack files are allowed."}, HTTPStatus.BAD_REQUEST)
            return

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="mrpack-inspect-", suffix=".mrpack", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                remaining = content_length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise PackError("Upload ended before all bytes were received.")
                    temporary.write(chunk)
                    remaining -= len(chunk)
            result = parse_mrpack(temporary_path, "upload")
            result["source"]["name"] = filename
            self.send_json({"pack": result})
        except PackError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except OSError as error:
            self.send_json({"error": f"Unable to store upload temporarily: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            if temporary_path:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


class MrpackServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: ServerState) -> None:
        super().__init__(address, MrpackHandler)
        self.state = state


PAGE = r'''<!doctype html>
<html lang="en-us"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MRPACK Inspector</title><style>
:root{color-scheme:dark;--bg:#10141b;--panel:#18212c;--line:#34495e;--text:#e3edf7;--muted:#9fb3c8;--accent:#74d4be;--danger:#ffb4a9}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,"Microsoft YaHei",sans-serif}header{height:58px;padding:0 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;background:#141c26}.brand{display:flex;gap:10px;align-items:center;font-weight:700;font-size:17px}.brand span{color:var(--accent)}button,select,input{font:inherit;color:inherit;background:#202c39;border:1px solid var(--line);border-radius:5px;padding:8px 10px}button{cursor:pointer}button:hover{border-color:var(--accent)}main{max-width:1400px;margin:0 auto;padding:24px}.drop{display:grid;place-items:center;min-height:154px;border:1px dashed #54708c;border-radius:7px;background:var(--panel);text-align:center;padding:20px;transition:.15s}.drop.drag{border-color:var(--accent);background:#1b3040}.drop strong{display:block;font-size:16px;margin:8px}.drop p{margin:0;color:var(--muted)}.drop input{display:none}.toolbar{display:flex;gap:10px;align-items:center;margin:20px 0 12px}.toolbar input{width:min(470px,100%)}.tabs{display:flex;gap:6px;border-bottom:1px solid var(--line);margin-top:22px}.tab{border-bottom:0;border-radius:5px 5px 0 0;color:var(--muted)}.tab.active{background:var(--panel);color:var(--accent)}.view{display:none;padding-top:16px}.view.active{display:block}.cards{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:10px}.card{border:1px solid var(--line);border-radius:6px;background:var(--panel);padding:14px}.card .label{color:var(--muted);font-size:12px}.card .value{font-size:18px;font-weight:700;margin-top:4px;overflow-wrap:anywhere}.summary{padding:14px;border:1px solid var(--line);border-radius:6px;background:var(--panel);margin-bottom:12px}.summary h2{font-size:17px;margin:0 0 4px}.summary p{margin:0;color:var(--muted)}table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:6px;overflow:hidden}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #293847;vertical-align:top}th{color:var(--accent);font-weight:600;background:#1c2733}td{max-width:520px;overflow-wrap:anywhere}tr:last-child td{border-bottom:0}.notice{margin:16px 0;padding:10px 12px;border-left:3px solid var(--danger);background:#302229;color:#ffd6cf}.hidden{display:none}@media(max-width:720px){header{padding:0 14px}.cards{grid-template-columns:repeat(2,1fr)}main{padding:14px}.toolbar{align-items:stretch;flex-direction:column}th,td{padding:7px}.wide{overflow-x:auto}}
</style><style>:root[data-theme="light"]{color-scheme:light;--bg:#f3f6f8;--panel:#ffffff;--line:#b8c7d2;--text:#18242d;--muted:#526775;--accent:#137d69;--danger:#a92c32}:root[data-theme="light"] header{background:#f9fbfc}:root[data-theme="light"] button,:root[data-theme="light"] select,:root[data-theme="light"] input{background:#eef3f6}:root[data-theme="light"] th{background:#eef3f6}:root[data-theme="light"] .notice{background:#fff0f0;color:#7e2026}.tree{max-height:65vh;overflow:auto;padding:10px 14px;border:1px solid var(--line);border-radius:6px;background:var(--panel)}.tree details{margin-left:17px}.tree>details{margin-left:0}.tree summary{cursor:pointer;padding:2px 0;color:var(--text);overflow-wrap:anywhere}.tree .leaf{display:block;margin-left:34px;padding:2px 0;overflow-wrap:anywhere}#theme{min-width:40px;padding-inline:10px}@media(prefers-reduced-motion:no-preference){body,header,button,select,input,.drop,.card,.summary,table,.tree,.notice{transition:background-color .2s ease,color .2s ease,border-color .2s ease,transform .2s ease,box-shadow .2s ease}.drop:hover,.card:hover{transform:translateY(-2px);border-color:var(--accent);box-shadow:0 8px 22px rgba(0,0,0,.12)}.tab:hover{transform:translateY(-1px);color:var(--accent)}.view.active{animation:fade-in .22s ease-out}.card{animation:fade-in .3s ease both}.card:nth-child(2){animation-delay:.03s}.card:nth-child(3){animation-delay:.06s}.card:nth-child(4){animation-delay:.09s}.card:nth-child(5){animation-delay:.12s}.tree details[open]>summary{color:var(--accent)}@keyframes fade-in{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}}</style></head><body><header><div class="brand">📦 <span id="brand">MRPACK Inspector</span></div><select id="language"><option value="en-us">English</option><option value="zh-cn">简体中文</option><option value="zh-tw">繁體中文</option></select></header><main>
<label class="drop" id="drop"><input id="file" type="file" accept=".mrpack"><div>📂<strong id="drop-title"></strong><p id="drop-copy"></p></div></label><div id="notice" class="notice hidden"></div><section id="content" class="hidden"><div id="summary" class="summary"></div><div id="cards" class="cards"></div><nav class="tabs"><button class="tab active" data-view="deps" id="tab-deps"></button><button class="tab" data-view="files" id="tab-files"></button><button class="tab" data-view="archive" id="tab-archive"></button></nav><section class="view active" id="view-deps"><div class="wide"><table><thead><tr><th id="h-dependency"></th><th id="h-version"></th></tr></thead><tbody id="deps"></tbody></table></div></section><section class="view" id="view-files"><div class="toolbar"><input id="filter"><select id="side"><option value="all" id="side-all"></option><option value="both" id="side-both"></option><option value="client_only" id="side-client"></option><option value="server_only" id="side-server"></option></select></div><div class="wide"><table><thead><tr><th id="h-path"></th><th id="h-side"></th><th id="h-client"></th><th id="h-server"></th><th id="h-hash"></th></tr></thead><tbody id="files"></tbody></table></div></section><section class="view" id="view-archive"><div class="wide"><table><thead><tr><th id="h-root"></th><th id="h-entries"></th><th id="h-size"></th></tr></thead><tbody id="roots"></tbody></table></div></section></section></main>
<script>
const MAX=__MAX_UPLOAD_MIB__, I18N={
"en-us":{brand:"MRPACK Inspector",dropTitle:"Choose or drop an MRPACK",dropCopy:`Only .mrpack files. Upload limit: ${MAX} MiB. Files are deleted after inspection.`,deps:"🔗 Dependencies",files:"🗂️ Indexed Files",archive:"📦 Archive",dependency:"🔗 Dependency",version:"🏷️ Version",path:"📄 Path",side:"↔️ Side",client:"🖥️ Client",server:"🗄️ Server",hash:"🔐 SHA-512",root:"🗂️ Archive root",entries:"🔢 Entries",size:"📦 Uncompressed size",filter:"🔍 Filter by path or side",all:"All sides",both:"🔁 Both",clientOnly:"🖥️ Client only",serverOnly:"🗄️ Server only",format:"Format",game:"Game",packSize:"Package size",indexed:"Indexed files",archiveEntries:"Archive entries",required:"✅ Required",optional:"⚪ Optional",unsupported:"🚫 Unsupported",uploading:"⏳ Inspecting upload…",loadError:"❌ {error}",sourceServer:"Server default",sourceUpload:"Browser upload"},
"zh-cn":{brand:"MRPACK 检查器",dropTitle:"选择或拖放 MRPACK 文件",dropCopy:`仅允许 .mrpack 文件。上传上限：${MAX} MiB。解析后立即删除上传文件。`,deps:"🔗 依赖项",files:"🗂️ 索引文件",archive:"📦 归档内容",dependency:"🔗 依赖项",version:"🏷️ 版本",path:"📄 路径",side:"↔️ 环境侧别",client:"🖥️ 客户端",server:"🗄️ 服务端",hash:"🔐 SHA-512",root:"🗂️ 归档根目录",entries:"🔢 条目数",size:"📦 未压缩大小",filter:"🔍 按路径或环境侧别筛选",all:"全部侧别",both:"🔁 双端",clientOnly:"🖥️ 仅客户端",serverOnly:"🗄️ 仅服务端",format:"格式",game:"游戏",packSize:"包大小",indexed:"索引文件",archiveEntries:"归档条目",required:"✅ 必需",optional:"⚪ 可选",unsupported:"🚫 不支持",uploading:"⏳ 正在解析上传文件…",loadError:"❌ {error}",sourceServer:"服务器默认包",sourceUpload:"浏览器上传"},
"zh-tw":{brand:"MRPACK 檢查器",dropTitle:"選擇或拖放 MRPACK 檔案",dropCopy:`僅允許 .mrpack 檔案。上傳上限：${MAX} MiB。解析後立即刪除上傳檔案。`,deps:"🔗 相依項目",files:"🗂️ 索引檔案",archive:"📦 封存內容",dependency:"🔗 相依項目",version:"🏷️ 版本",path:"📄 路徑",side:"↔️ 環境側別",client:"🖥️ 用戶端",server:"🗄️ 伺服端",hash:"🔐 SHA-512",root:"🗂️ 封存根目錄",entries:"🔢 條目數",size:"📦 未壓縮大小",filter:"🔍 依路徑或環境側別篩選",all:"全部側別",both:"🔁 雙端",clientOnly:"🖥️ 僅用戶端",serverOnly:"🗄️ 僅伺服端",format:"格式",game:"遊戲",packSize:"包大小",indexed:"索引檔案",archiveEntries:"封存條目",required:"✅ 必要",optional:"⚪ 選用",unsupported:"🚫 不支援",uploading:"⏳ 正在解析上傳檔案…",loadError:"❌ {error}",sourceServer:"伺服器預設包",sourceUpload:"瀏覽器上傳"}}
I18N["en-us"].tree="🌳 File Tree";I18N["zh-cn"].tree="🌳 文件树";I18N["zh-tw"].tree="🌳 檔案樹";I18N["en-us"].system="Follow system";I18N["zh-cn"].system="跟随系统";I18N["zh-tw"].system="跟隨系統";I18N["en-us"].themeSystem="Follow system theme";I18N["zh-cn"].themeSystem="跟随系统主题";I18N["zh-tw"].themeSystem="跟隨系統主題";I18N["en-us"].themeDark="Dark theme";I18N["zh-cn"].themeDark="深色主题";I18N["zh-tw"].themeDark="深色主題";I18N["en-us"].themeLight="Light theme";I18N["zh-cn"].themeLight="浅色主题";I18N["zh-tw"].themeLight="淺色主題";
function systemLanguage(){const value=(navigator.language||"en-us").toLowerCase();return value.startsWith("zh-tw")||value.startsWith("zh-hk")||value.startsWith("zh-mo")?"zh-tw":value.startsWith("zh")?"zh-cn":"en-us"}let languageMode="system",lang=systemLanguage(),themeMode="system",data=null;const $=id=>document.getElementById(id), esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));const t=k=>I18N[lang][k]||I18N["en-us"][k]||k;
function setNotice(text=""){const e=$("notice");e.textContent=text;e.classList.toggle("hidden",!text)}function sideLabel(group){return group==="both"?t("both"):group==="client_only"?t("clientOnly"):group==="server_only"?t("serverOnly"):"🚫"}function envLabel(value){return value==="required"?t("required"):value==="optional"?t("optional"):t("unsupported")}
function setupTreeTab(){const tab=document.createElement("button");tab.className="tab";tab.dataset.view="tree";tab.id="tab-tree";const view=document.createElement("section");view.className="view";view.id="view-tree";view.innerHTML='<div class="tree" id="tree"></div>';document.querySelector(".tabs").append(tab);document.querySelector("#content").append(view);tab.addEventListener("click",()=>{document.querySelectorAll(".tab,.view").forEach(e=>e.classList.remove("active"));tab.classList.add("active");view.classList.add("active")})}
function applyTheme(){const dark=themeMode==="dark"||(themeMode==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.dataset.theme=dark?"dark":"light";const button=$("theme");button.textContent=themeMode==="system"?"🖥️":dark?"🌙":"☀️";button.title=button.ariaLabel=themeMode==="system"?t("themeSystem"):dark?t("themeDark"):t("themeLight")}
function setupControls(){const select=$("language"),system=document.createElement("option");system.value="system";system.id="language-system";select.prepend(system);select.value=languageMode;select.addEventListener("change",event=>{languageMode=event.target.value;lang=languageMode==="system"?systemLanguage():languageMode;applyTheme()});const button=document.createElement("button");button.id="theme";button.type="button";button.addEventListener("click",()=>{themeMode=themeMode==="system"?"dark":themeMode==="dark"?"light":"system";applyTheme()});document.querySelector("header").append(button);system.textContent=t("system");applyTheme();addEventListener("languagechange",()=>{if(languageMode==="system"){lang=systemLanguage();applyLanguage()}});matchMedia("(prefers-color-scheme: dark)").addEventListener("change",()=>{if(themeMode==="system")applyTheme()})}
queueMicrotask(()=>$("language").addEventListener("change",()=>$("language-system").textContent=t("system")));
function buildTree(paths){const root={folders:{},files:[]};for(const path of paths||[]){const raw=String(path),parts=raw.split("/").filter(Boolean),isDirectory=raw.endsWith("/");let node=root;parts.forEach((part,index)=>{if(isDirectory||index<parts.length-1)node=node.folders[part]||(node.folders[part]={folders:{},files:[]});else node.files.push(part)})}return root}
function renderTree(pack){$("tab-tree").textContent=t("tree");const renderNode=node=>Object.keys(node.folders).sort().map(name=>`<details><summary>📁 ${esc(name)}</summary>${renderNode(node.folders[name])}</details>`).join("")+node.files.sort().map(name=>`<span class="leaf">📄 ${esc(name)}</span>`).join("");$("tree").innerHTML=`<details open><summary>📦 ${esc(pack.source.name)}</summary>${renderNode(buildTree(pack.archive_paths))}</details>`}
setupTreeTab();setupControls();
function applyLanguage(){document.documentElement.lang=lang;document.title=t("brand");$("brand").textContent=t("brand");$("drop-title").textContent=t("dropTitle");$("drop-copy").textContent=t("dropCopy");$("tab-deps").textContent=t("deps");$("tab-files").textContent=t("files");$("tab-archive").textContent=t("archive");[["h-dependency","dependency"],["h-version","version"],["h-path","path"],["h-side","side"],["h-client","client"],["h-server","server"],["h-hash","hash"],["h-root","root"],["h-entries","entries"],["h-size","size"],["side-all","all"],["side-both","both"],["side-client","clientOnly"],["side-server","serverOnly"]].forEach(([id,k])=>$(id).textContent=t(k));$("filter").placeholder=t("filter");if(data)render(data)}
function render(pack){data=pack;$("content").classList.remove("hidden");setNotice("");const s=pack.stats,m=pack.metadata,source=pack.source;$("summary").innerHTML=`<h2>📦 ${esc(source.name)}</h2><p>${source.kind==="server"?t("sourceServer"):t("sourceUpload")} · ${t("format")} ${esc(m.format_version)} · ${t("game")} ${esc(m.game)}</p>`;const cards=[["📦",t("packSize"),source.size_label],["📊",t("indexed"),s.indexed_files],["🔁",t("both"),s.both],["🖥️",t("clientOnly"),s.client_only],["📦",t("archiveEntries"),s.archive_entries]];$("cards").innerHTML=cards.map(c=>`<div class="card"><div class="label">${c[0]} ${esc(c[1])}</div><div class="value">${esc(c[2])}</div></div>`).join("");$("deps").innerHTML=pack.dependencies.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.version)}</td></tr>`).join("")||"<tr><td colspan=\"2\">—</td></tr>";$("roots").innerHTML=pack.archive_roots.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.entries)}</td><td>${esc(x.size_label)}</td></tr>`).join("");renderFiles()}
function renderFiles(){if(!data)return;const needle=$("filter").value.toLowerCase(),side=$("side").value;const rows=data.files.filter(x=>{const search=`${x.path} ${x.group} ${x.client} ${x.server}`.toLowerCase();return(!needle||search.includes(needle))&&(side==="all"||x.group===side)});$("files").innerHTML=rows.map(x=>`<tr><td>${esc(x.path)}</td><td>${esc(sideLabel(x.group))}</td><td>${esc(envLabel(x.client))}</td><td>${esc(envLabel(x.server))}</td><td>${esc(x.sha512.slice(0,16))}</td></tr>`).join("")||"<tr><td colspan=\"5\">—</td></tr>";renderTree(data)}
async function upload(file){if(!file.name.toLowerCase().endsWith(".mrpack")){setNotice(t("loadError").replace("{error}","Only .mrpack files are allowed."));return}if(file.size>MAX*1024*1024){setNotice(t("loadError").replace("{error}",`Upload exceeds ${MAX} MiB.`));return}setNotice(t("uploading"));try{const response=await fetch("/api/inspect",{method:"POST",headers:{"Content-Type":"application/octet-stream","X-MRPACK-Filename":encodeURIComponent(file.name)},body:file});const payload=await response.json();if(!response.ok)throw Error(payload.error||response.statusText);render(payload.pack)}catch(error){setNotice(t("loadError").replace("{error}",error.message))}}
$("file").addEventListener("change",e=>e.target.files[0]&&upload(e.target.files[0]));$("drop").addEventListener("dragover",e=>{e.preventDefault();$("drop").classList.add("drag")});$("drop").addEventListener("dragleave",()=>$("drop").classList.remove("drag"));$("drop").addEventListener("drop",e=>{e.preventDefault();$("drop").classList.remove("drag");e.dataTransfer.files[0]&&upload(e.dataTransfer.files[0])});$("filter").addEventListener("input",renderFiles);$("side").addEventListener("change",renderFiles);document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll(".tab,.view").forEach(e=>e.classList.remove("active"));b.classList.add("active");$("view-"+b.dataset.view).classList.add("active")}));$("language").addEventListener("change",e=>{lang=e.target.value;applyLanguage()});applyLanguage();fetch("/api/current").then(r=>r.json()).then(p=>{if(p.pack)render(p.pack);else if(p.error)setNotice(t("loadError").replace("{error}",p.error))}).catch(()=>setNotice(t("loadError").replace("{error}","Unable to contact the local server.")));
</script></body></html>'''


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mrpack", type=Path, help="Server-local .mrpack file to pre-load")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT})")
    parser.add_argument("--max-upload-mib", type=int, default=DEFAULT_MAX_UPLOAD_MIB, help=f"Upload limit in MiB (default: {DEFAULT_MAX_UPLOAD_MIB})")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.max_upload_mib < 1:
        parser.error("--max-upload-mib must be at least 1")
    return args


def main() -> None:
    args = parse_arguments()
    state = ServerState(args.max_upload_mib * 1024 * 1024, args.mrpack)
    server = MrpackServer((args.host, args.port), state)
    if args.host in {"0.0.0.0", "::"}:
        lan_ips = sorted({address for address in socket.gethostbyname_ex(socket.gethostname())[2] if not address.startswith("127.")})
        locations = ", ".join(f"http://{address}:{args.port}" for address in lan_ips) or f"http://<LAN-IP>:{args.port}"
        print(f"Listening on all interfaces. LAN URLs: {locations}")
        print("Warning: uploads are unauthenticated. Only expose this to trusted networks.")
    else:
        print(f"Listening at http://{args.host}:{args.port}")
    print(f"Upload limit: {args.max_upload_mib} MiB")
    if state.current_error:
        print(f"Default MRPACK could not be loaded: {state.current_error}")
    elif args.mrpack:
        print(f"Pre-loaded: {args.mrpack}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
