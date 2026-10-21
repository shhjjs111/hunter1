# Android 独立版

> 目标形态：安装到安卓手机后**不需要电脑、不需要局域网**。岗位、投递、画像与会话
> 都存在手机本地；抓取招聘站与调用大模型时才需要互联网。

## 架构

```
frontend/
├── src/                 React SPA（桌面 Web 与安卓共用）
├── src/mobile/          安卓本地后端
│   ├── db.ts            IndexedDB（设置 / 岗位 / 投递 / 会话）
│   ├── crawlers.ts      站点注册表 + HTML 解析
│   ├── http.ts          CapacitorHttp（绕过 WebView CORS）
│   ├── llm.ts           模型探测 / 评分 / 助手
│   ├── localApi.ts      拦截 /api/*，返回与 FastAPI 相同形状的 JSON
│   └── sync.ts          与电脑端双向同步岗位 / 投递 / 画像
├── capacitor.config.ts  Capacitor 配置
└── android/             Capacitor 生成的安卓工程
```

- `main.tsx` 在原生平台调用 `installMobileBackend()`，把 `fetch('/api/...')`
  接到本地实现；桌面 Web 仍访问真实的 FastAPI 后端。
- 本地实现按现有 OpenAPI 契约返回字段（`snake_case`），所以页面组件与 hooks
  不需要分叉。
- 抓取与模型请求走 `CapacitorHttp`（原生网络栈），不受 WebView CORS 限制。
- 数据存在 WebView 的 IndexedDB 中；卸载 App 会清空数据。

## 构建

前置：Node 20+、JDK 21、Android SDK 36（platform + build-tools）。
把下面两处替换成你本机的实际安装位置：

```powershell
$env:JAVA_HOME = "C:\path\to\hunter1\.tools\jdks\jdk21\jdk-21.0.12.1+1"
$env:ANDROID_HOME = "C:\path\to\hunter1\.tools\android\sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
```

```powershell
cd frontend
npm ci
npm run build
npx cap sync android
cd android
.\gradlew.bat assembleDebug
```

产物：`frontend/android/app/build/outputs/apk/debug/app-debug.apk`。

- Debug APK 使用调试签名，可直接侧载；适合先给亲友试用。
- 正式分发建议生成自己的 release keystore，并用 `assembleRelease` + `apksigner`
  签名。**keystore 与密码不要提交到仓库**；丢失后无法覆盖安装升级。

### 国内网络

Gradle wrapper 默认从 `services.gradle.org` 下载（会跳到 GitHub）。如果超时，
可临时把 `frontend/android/gradle/wrapper/gradle-wrapper.properties` 的
`distributionUrl` 换成镜像，例如：

```
https://mirrors.cloud.tencent.com/gradle/gradle-8.14.3-all.zip
```

`gradle-wrapper.properties` 固定了官方发行包的 `distributionSha256Sum`；
镜像必须提供与官方完全一致的文件，否则 wrapper 会拒绝使用。

Android SDK 包也可通过 `sdkmanager` 的代理/Mirror 配置下载。

## 已产出的 APK

| 用途 | 路径 |
|---|---|
| 调试 / 快速试用 | `frontend/android/app/build/outputs/apk/debug/app-debug.apk` |
| 正式分发（release 签名） | `dist/hunter1-android-0.4.1.apk` |

本次 release 包：

- `versionName=0.4.1`、`versionCode=2`
- `minSdk=24`、`targetSdk=36`
- SHA256：`53BE1A3E34A751CD7B9B3F3E6AEFBB418C9307CEFACE2849C791A4820056A3A6`

正式 APK 随 [GitHub Release](https://github.com/shhjjs111/hunter1/releases) 提供；
本地构建产物在 `dist/`，不进入 Git 仓库。

**签名证书 SHA-256 指纹**（v0.4.1 起）：

```
87f08004b20d32403fb6483a8717b6bf75a99e9c173a9324cb52c848c61aa4d1
```

核对方式（Android SDK build-tools 自带）：

```bash
apksigner verify --print-certs dist/hunter1-android-0.4.1.apk
```

### 供应链

从下一个 `v*` tag 起，`release.yml` 会：

1. 用 `-PappVersion=<tag>` 构建 `assembleRelease`；`versionCode` 由
   `major*1_000_000 + minor*1_000 + patch` 派生（0.4.1 → 4001）；
2. 用 `ANDROID_KEYSTORE_BASE64` 等 secrets 签名（缺失则发布失败，不会偷偷发
   未签名包）；
3. 运行 `scripts/verify_apk.py` 校验包名、版本、`debuggable`、`allowBackup`、
   权限与证书指纹（`frontend/android/EXPECTED_CERT_SHA256`）；
4. 把 APK 写进 `SHA256SUMS.txt`、SPDX SBOM、build provenance 与 SBOM
   attestation。

v0.4.1 的 APK 是这条链之前手工构建的，因此不在该版本的 SHA256SUMS / SBOM
中；从下一个 tag 起上述覆盖成立。

签名密钥与密码保存在本机 `.tools/android/`（`hunter1-release.jks` 与
`keystore-password.txt`），**不在仓库里**。请自行备份；丢失后无法覆盖安装升级，
只能卸载重装（本地数据会丢）。

## 使用

1. 手机允许「安装未知来源应用」，安装 APK；
2. 打开 App → 「配置」填 `base_url` / 模型 / API Key；
3. 「抓取」跑一轮（需要互联网）；
4. 岗位库查看、评分、记录投递。

所有数据都在手机上；不需要运行 `hunter1.exe`，也不需要同一 Wi-Fi。

## 与电脑同步（可选）

手机独立版平时不依赖电脑；想在两边使用同一份岗位与投递记录时，可以在同一
Wi-Fi 下同步：

1. 电脑上双击 `hunter1-lan.cmd`（或运行 `hunter1 serve --lan`）；
2. 记下终端打印的「手机访问」地址与「访问码」；
3. 手机 App →「配置」→「电脑同步」，填入地址与访问码；
4. 点「双向同步」（也可以只「从电脑拉取」或只「推送到电脑」）。

同步内容：

- 岗位事实与评分结论（按详情页 URL 对齐，不按 UUID）；
- 投递记录（按岗位详情页 URL 对齐，`updated_at` 较新的阶段/备注胜出）；
- 候选人画像（`updated_at` 较新的胜出）。

**不搬**：API Key、助手会话历史、运行态抓取进度。它们只留在各自设备上。

冲突规则是「较新的一方胜」，不做逐字段人工合并；两台设备同时改同一条记录时，
以时间戳较晚的为准。同步请求走 `X-Hunter1-Access-Code` 请求头，和浏览器
输入访问码是同一个口令，仍然只适用于「同一 Wi-Fi + 自己家的网络」信任模型。

同步是**合并**而不是镜像：它只新增/更新对端已有的记录，**不会把对端的删除
传播过来**。想彻底清掉一条投递，请在两边分别删除。

## 每日补抓与抓取节奏

- **打开 App 时自动补抓**：首次打开、以及从后台切回前台时会检查一次；距离上次
  成功抓取满 24 小时才真正启动，同一时刻只会跑一轮。没有做 Android 后台定时
  任务 —— 数据在 WebView 的 IndexedDB 里，后台 Worker 无法可靠地读写它；「回到
  前台补抓」是当前形态下不会丢数据的选择。
- **失败冷却**：一轮全部失败后 1 小时内不再自动重试，避免断网时每次切前台都
  重打站点。
- **分页深度**：实习僧抓 5 页（每页 20 条，实测页 2–5 内容各不相同）；万行匿名
  HTML 固定只输出第一页 10 条（翻页由登录态 JS API 驱动），猎聘校园、高校人才网、
  应届生的公开落地页也没有翻页入口，均保持单页。页数集中写在
  `frontend/src/mobile/crawlers.ts` 的 `SITES.maxPages`，观察站点限流后再调。
- **请求间隔**：列表页之间 0.9–1.25 秒、详情页之间 0.7–1.05 秒（带随机抖动），
  避免固定节奏被识别成脚本。
- **只补缺失 JD**：已有 `jd_raw` 的岗位下一轮不再请求详情；每站每轮最多补 60 条，
  没轮到的保持 `pending`，下一轮继续，不会反复抓同一批。

## 当前边界

- 安卓独立版目前是**前台抓取**：App 被系统挂起时抓取可能暂停；重新打开/切回
  前台会自动补抓（最多每 24 小时一轮），也可在「抓取」页手动再跑。
- 抓取适配器与后端 `slices/crawl/sites.py` 同源，但站点改版时仍需同步更新选择器。
- 助手在移动端走**非流式**请求：回答会一次性出现，暂不支持逐字输出与工具调用。
- 暂无自动备份/定时同步：同步是手动按钮，且需要电脑端 `--lan` 在运行；
  卸载 App 仍会清空手机本地数据。
- 目前只构建 Android；iOS 需要额外的 Xcode/签名流程。
