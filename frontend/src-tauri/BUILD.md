# A4S 桌面端打包（Tauri）

本目录是 Tauri 2 shell，把 `frontend/` 的 Vite 应用包成 Windows 桌面程序。
主窗口加载生产构建（`../dist`），开发时用 `devUrl` 连 `vite` 的 5173 端口。

## 当前环境阻塞（重要）

本机 **未安装 Rust 工具链**（`cargo` / `rustc` 均不在 PATH），因此
`tauri dev` / `tauri build` 目前**无法在本机执行**。已交付的是
**可构建骨架**：Cargo 工程、`tauri.conf.json`、托盘入口、图标占位、npm 脚本均就位，
安装 Rust 后即可直接构建。

## 一次性准备

1. 安装 Rust（含 MSVC 目标）：`winget install Rustlang.Rustup`，然后
   `rustup default stable-msvc`。
2. 安装 Visual Studio Build Tools（C++ 生成工具）。
3. 安装前端依赖（含 Tauri CLI）：
   ```powershell
   cd frontend
   npm install
   ```
4. （可选，推荐）用应用图标生成完整尺寸图标集：
   ```powershell
   npx tauri icon logo/A4s.png
   ```
   当前 `icons/icon.png` 为占位（由 `logo/A4s.png` 复制）。

## 开发

```powershell
cd frontend
npm run tauri:dev
```

会先起 Vite（5173），再拉起桌面窗口；后端 `client:8000` 需另行启动：

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn client.main:app --port 8000
```

## 构建安装包

```powershell
cd frontend
npm run tauri:build
```

产物在 `src-tauri/target/release/bundle/`。

## 说明

- 托盘：左键点击托盘图标显示并聚焦主窗口（见 `src/main.rs`）。
- 开机自启：可选开关，当前未接入 `tauri-plugin-autostart`；如需启用，
  在 `Cargo.toml` 增加该插件并在 `setup` 中按配置调用 enable/disable。
- 模型配置页：由前端设置页承担（`frontend/src` 内已有设置路由），Tauri 仅做外壳。
