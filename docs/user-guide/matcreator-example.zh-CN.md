# MatCreator：原生 Agent 研究工作区

[English documentation](../../plugins/matcreator/README.md) | **简体中文**

MatCreator 卡包提供专属 **MatCreator Agent** 和 **MatCreator native research** Legion。
专属卡片使用上游 `devel` 分支的规划器、执行协调器和递归子 Agent；计算、知识、产物和展示通过 OAW 卡片连接。
原来的拼装式 Legion、专用研究任务板和示例创建脚本已移除，卡包不再依赖 Barracks。

## 安装与部署

正常 OAW 安装、开发启动和桌面打包会自动准备 MatCreator 运行环境，无需单独运行安装脚本，
也无需填写卡片的 Python 路径。首次安装需要联网下载依赖，后续复用已验证的环境。
运行环境由卡包管理并与 OAW 的 Python 依赖隔离；主程序 Python 低于 3.12 时会自动准备所需解释器。

安装时会准备依赖和 LiteLLM 的公开分词缓存。运行时固定为 `devel` 的
`0328c8b814499cbf3e49116323cee050145fa08f`；源码不匹配时会明确报错。

1. 重启后端并重建或重新加载前端。
2. 在 Library 打开 MatCreator 卡包，将 **MatCreator native research** 加入牌组并放到画布。
3. 打开 Legion 的 **Workspace mode**。
4. 在 **Settings → Models** 设置默认模型，并配置连接的 Sandbox。

卡片的 **MatCreator Python override** 和 **MatCreator checkout** 是开发调试用的可选覆盖项，通常留空。
安装和启动必须使用同一个 Python 环境。Windows 的 `scripts/dev.ps1` 已统一使用
`backend/.venv/Scripts/python.exe`，不会再通过 `uv run --with-editable` 创建临时后端环境。
若提示卡包安装不完整，错误中会列出后端 Python 和查找目录；先核对启动环境，避免反复安装到另一个环境。

模型密钥由 OAW 管理，不写入卡片或 Legion 模板。材料软件、环境变量与远程计算凭据配置在 Sandbox 中。
原生网页服务的远程任务监控不随卡片启动；Agent 应通过已连接工具检查远程任务，并保留任务 ID。

## 工作区布局

新版预设为 revision 2：

| 区域 | 内容 |
| --- | --- |
| 左侧 | **Agent graph**：主 Agent、规划器、执行器和子 Agent 的关系与状态 |
| 中间 | **Conversation**、**Skills & knowledge**、**Structure viewer**、**File preview**、**Research artifacts**、**Research files & compute** 标签 |
| 右侧上方 | **Sessions**，选择或创建会话 |
| 右侧下方 | **Files**，浏览 Sandbox 文件 |

执行图的 **Agents** 显示父子调用和依赖，**Plan** 显示原生计划 DAG。
可以缩放、点击节点查看详情与结果；切换会话会切换图和原生执行状态。
它是执行状态的展示，任务执行仍由 MatCreator 后端管理。

**Agent graph** 是 Core essentials 中的通用独立卡片。通过 **Observe execution**
将图卡连接到普通 Agent、Agent Barracks、Summoning 或其他执行来源，再在图卡中选择来源。
实线表示父子调用，虚线表示任务依赖；两者在详情中分别标明。
其他卡片或运行时可以使用[公共执行图接口](../developers/execution-graph.md)提供自己的数据。

默认 **normal** 模式先在会话中给出计划，确认后执行；**flash** 模式直接委派。
例如：“规划一个铜超胞研究，先检查科学计算环境，生成结构后核验原子数并发布产物。”
执行成功不等于科学结果已通过验收，仍需核查输出、单位与计算假设。

## 文件与标签联动

在右侧点击文件，会打开中间的 **File preview**。
如果已连接的 Structure viewer 成功读取并解析该结构，会自动切到 **Structure viewer**；
从会话附件打开结构时，临时文件预览弹窗也会随之关闭。
普通文本、无效结构和不受支持的文件继续使用普通预览。

结构视图固定为 **Pinned**、重新加载、后台刷新或重新连接时，不会自动切换标签。
切换标签保留会话草稿、文件选择和卡片内部状态。

联动通过本体公共 API `host.workspace.activate()` 实现，仅影响当前浏览器中打开的工作区。
它不会自动保存或重排布局，不会恢复用户隐藏的 section，也不会在编辑布局时抢走标签。
插件接入方式见[工作区设计文档](../developers/workspace-design.md#activate-content-after-an-operation)。

## 升级与验证范围

新部署使用新版布局。现有保存布局由用户管理，不会被预设更新覆盖；可以重新部署，或用 **Edit layout** 调整。
移除旧功能不会删除已有世界记录，也不会把旧的 OAW Agent 会话自动转换为 MatCreator 会话。

本地测试覆盖原生编排与模拟模型、会话隔离、权限撤销、产物核验、执行图、工作区标签联动和真实结构渲染。
这些测试不代替真实模型、Bohrium 或科学计算验收。
