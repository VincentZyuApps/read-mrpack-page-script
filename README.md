# 📦 MRPACK 检查器

🧭 使用 Python 标准库构建的单文件 WebUI，用于读取和浏览 Modrinth `.mrpack` 整合包。

[![Modrinth](https://img.shields.io/badge/Modrinth-1BD96A?style=for-the-badge&logo=modrinth&logoColor=white)](https://support.modrinth.com/en/articles/8802250-modpacks-on-modrinth)

## ✨ 功能

- 📂 支持启动参数预加载本地 `.mrpack`，也支持浏览器选择或拖放上传。
- 🔗 展示 Minecraft、Fabric Loader 等整合包依赖项。
- 🗂️ 展示 Modrinth 索引文件，并可按路径或客户端 / 服务端侧别筛选。
- 📦 展示 ZIP 归档根目录统计与完整可折叠文件树。
- 🌐 提供简体中文、繁体中文、英文和跟随系统语言模式。
- 🖥️ 提供跟随系统、深色、浅色三种主题模式。

## 🚀 启动

💡 环境要求：Python 3.11 或更高版本；不需要安装第三方依赖。

```powershell
python .\read_mrpack_page.py
```

🔗 默认访问地址：`http://127.0.0.1:60907`

```powershell
python .\read_mrpack_page.py --mrpack 'G:\GGames\Minecraft\aaaSERVERSaaa\aaaMRPACKSaaa\Cobblemon Official Modpack [Fabric] 1.8.mrpack'
```

📡 局域网访问时使用下列命令；上传接口没有认证，仅应暴露给可信网络。

```powershell
python .\read_mrpack_page.py --host 0.0.0.0 --port 60907 --mrpack 'G:\GGames\Minecraft\aaaSERVERSaaa\aaaMRPACKSaaa\Cobblemon Official Modpack [Fabric] 1.8.mrpack'
```

## ⚙️ 参数

| 🏷️ 参数 | 📝 说明 |
| --- | --- |
| `--mrpack PATH` | 📂 启动时读取的本地 `.mrpack` 文件。 |
| `--host HOST` | 🌐 监听地址，默认 `127.0.0.1`。 |
| `--port PORT` | 🔌 监听端口，默认 `60907`。 |
| `--max-upload-mib SIZE` | 📏 浏览器上传上限，默认 `1024` MiB。 |

## 🖼️ 预览

### 🔗 依赖项

![依赖项界面](docs/images/preview/依赖项-dependencies.png)

### 🗂️ 索引文件

![索引文件界面](docs/images/preview/索引文件-indexed-files.png)

### 📦 归档内容

![归档内容界面](docs/images/preview/归档内容-archive.png)

### 📊 文件数

![文件数界面](docs/images/preview/文件数-file-count.png)

## 🔒 安全边界

- ✅ 只接受 `.mrpack` 扩展名，且要求归档内存在 `modrinth.index.json`。
- ✅ 限制索引 JSON 最大为 16 MiB，归档条目最大为 100,000 个。
- ✅ 上传文件在解析完成后立即删除，不会保存整合包副本。
- ⚠️ 局域网模式没有访问认证，请勿直接暴露到公网。

---

# 📦 MRPACK Inspector

🧭 A single-file WebUI built with the Python standard library for reading and browsing Modrinth `.mrpack` modpacks.

[![Modrinth](https://img.shields.io/badge/Modrinth-1BD96A?style=for-the-badge&logo=modrinth&logoColor=white)](https://support.modrinth.com/en/articles/8802250-modpacks-on-modrinth)

## ✨ Features

- 📂 Preload a local `.mrpack` through a startup argument, or select and drop one in the browser.
- 🔗 Display modpack dependencies such as Minecraft and Fabric Loader.
- 🗂️ Display Modrinth indexed files, with filtering by path or client / server side.
- 📦 Display ZIP archive root statistics and a complete collapsible file tree.
- 🌐 Provide Simplified Chinese, Traditional Chinese, English, and a follow-system language mode.
- 🖥️ Provide follow-system, dark, and light theme modes.

## 🚀 Start

💡 Requirement: Python 3.11 or newer; no third-party dependency installation is required.

```powershell
python .\read_mrpack_page.py
```

🔗 Default address: `http://127.0.0.1:60907`

```powershell
python .\read_mrpack_page.py --mrpack 'G:\GGames\Minecraft\aaaSERVERSaaa\aaaMRPACKSaaa\Cobblemon Official Modpack [Fabric] 1.8.mrpack'
```

📡 Use the command below for LAN access; the upload endpoint has no authentication and must only be exposed to trusted networks.

```powershell
python .\read_mrpack_page.py --host 0.0.0.0 --port 60907 --mrpack 'G:\GGames\Minecraft\aaaSERVERSaaa\aaaMRPACKSaaa\Cobblemon Official Modpack [Fabric] 1.8.mrpack'
```

## ⚙️ Arguments

| 🏷️ Argument | 📝 Description |
| --- | --- |
| `--mrpack PATH` | 📂 Local `.mrpack` file to read at startup. |
| `--host HOST` | 🌐 Bind address; defaults to `127.0.0.1`. |
| `--port PORT` | 🔌 Bind port; defaults to `60907`. |
| `--max-upload-mib SIZE` | 📏 Browser upload limit; defaults to `1024` MiB. |

## 🖼️ Preview

### 🔗 Dependencies

![Dependencies view](docs/images/preview/依赖项-dependencies.png)

### 🗂️ Indexed Files

![Indexed files view](docs/images/preview/索引文件-indexed-files.png)

### 📦 Archive

![Archive view](docs/images/preview/归档内容-archive.png)

### 📊 File Count

![File count view](docs/images/preview/文件数-file-count.png)

## 🔒 Security Boundaries

- ✅ Only accepts the `.mrpack` extension and requires `modrinth.index.json` inside the archive.
- ✅ Limits index JSON to 16 MiB and archive entries to 100,000.
- ✅ Deletes uploaded files immediately after inspection and does not retain a modpack copy.
- ⚠️ LAN mode has no access authentication. Do not expose it directly to the public internet.
