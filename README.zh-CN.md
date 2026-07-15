# skman

一个极简的命令行工具,用于管理编码 Agent 使用的 skills —— 兼容 **Claude Code**、
**Codex CLI**,以及任何从 `~/.agents/skills` 加载 skill 的 Agent
(跨 Agent 通用目录;`skills.sh` / `npx skills` 也安装到这里)。

> English version: [README.md](README.md)

## 功能简介

1. **下载** 来自 git 仓库(或本地目录)的 skills。
2. **按需同步** —— 拉取上游、刷新状态、更新符号链接。
3. **建立符号链接** 到 `~/.agents/skills` 和 `~/.claude/skills`。
   Codex 通过其跨 Agent 回退机制自动识别 `~/.agents/skills` 中的 skill。
   这些目录会在首次同步时自动创建,无需提前配置。
4. **跟踪状态**,存储在 `~/.skman/state.json` 中:每个 skill 的 slug、
   名称、描述、来源、短 commit id、安装/最近同步时间以及是否启用。
5. **消除歧义**:为来自不同来源的 skill 在符号链接名后追加一个由
   来源 URL 派生出的短 id,使两个来源的同名 skill 可以共存。
   当 `(name, description)` 在多个来源中完全一致时,仍会打印警告,
   方便你发现真正的重复项。
6. **记录使用情况**:通过 Claude Code 和 Codex 的 `PreToolUse` 钩子
   记录调用,并提供聚合统计。

> 注意:用户编辑作为 patch 应用的能力目前不在范围内。

## 安装

### 一键安装(推荐)

```bash
curl -fsSL https://raw.githubusercontent.com/zhendong/skill-man/main/install.sh | sh
```

支持 macOS 和 Linux。安装脚本使用 [uv](https://docs.astral.sh/uv/)
获取 Python 工具链,并在隔离环境中从 PyPI 安装 `skman` —— 你不需要
预先安装 Python 或 pip。

环境变量:
- `SKMAN_FROM_GIT=1` —— 从 GitHub 仓库安装(而非 PyPI),搭配
  `SKMAN_REF=<branch-or-tag>` 指定分支或标签。
- `SKMAN_NO_UV=1` —— 回退到 `pipx`/`pip`,不使用 uv。

### 通过 pip / pipx / uv 安装

```bash
pipx install skman              # 推荐:全局安装 CLI
uv tool install skman           # 等价的 uv 命令
pip install --user skman        # 普通 pip
```

### 从源码安装

```bash
cd skill-man   # 仓库目录仍叫 skill-man,但工具名是 skman
pip install -e .          # 将 `skman` 暴露到 PATH
# 或者:
uv tool install .
```

也可以不安装直接运行:

```bash
python3 -m skman <args>
```

状态文件位于 `~/.skman/`(可通过 `$SKMAN_ROOT` 覆盖)。

### Windows

没有原生 Windows 版本。请使用 **WSL**(Windows Subsystem for Linux)
—— 安装一个发行版(Ubuntu/Debian 等),进入其 shell,在 Linux 环境
内运行上面的一键安装。你的 Agent CLI(Claude Code、Codex 等)也应该
在 WSL 内运行,这样 skman 的符号链接会落在 Linux 用户主目录,Agent
才能找到。

## 首次运行

安装完成后,最快进入可用状态的方式是:

```bash
skman setup
```

这会为已配置的 Agent 安装使用记录钩子,并把磁盘上已有的 skill 迁移过来
(详见下文)。重复执行是安全的。

## 快速开始

```bash
skman source add https://github.com/obra/superpowers.git           # slug 自动派生为 `superpowers`
skman sync                                              # 克隆仓库、查找 SKILL.md、建立链接

skman list                                              # 查看已管理的 skill(含安装/更新时间和 commit)
skman show brainstorming                                # 查看某个 skill 的描述
skman show brainstorming --all                          # 查看完整的 SKILL.md 内容
skman install-hook --write                              # 记录 skill 调用
skman stats                                             # 查看使用统计
```

没有 `init` 这一步。所有目录(包括 `~/.agents/skills` 和
`~/.claude/skills`)都会在第一次需要写入时自动创建。

### 源仓库布局约定

来源仓库遵循标准布局:顶层有一个 `skills/` 目录,每个 skill 一个文件夹,
其中包含 `SKILL.md` 以及辅助文件:

```
<source-repo>/
└── skills/
    ├── brainstorming/
    │   └── SKILL.md
    └── tdd/
        ├── SKILL.md
        └── examples/
```

skman 自动识别:如果源仓库根目录存在 `skills/`,就在其中扫描;否则
扫描整个仓库。允许子分类(如 `skills/foundations/tdd/`)——
`SKILL.md` 会被递归查找。

### 源仓库标识

你不需要自己起名字。slug 由 URL 的最后一段派生出来(小写、去除
`.git`、替换不安全字符):

| 输入 URL                                              | 派生 slug             |
|-------------------------------------------------------|-----------------------|
| `https://github.com/obra/superpowers.git`             | `superpowers`         |
| `git@github.com:obra/superpowers`                     | `superpowers`         |
| `/Users/me/dev/my-skills`                             | `my-skills`           |
| 第二个仓库末段同样是 `superpowers`                    | `superpowers-2`       |

重复添加同一个 URL 会报错 —— `https://h/o/r`、`https://h/o/r/`、
`https://h/o/r.git`、`git@h:o/r` 都视为同一来源。
用 `skman source remove <slug>` 或 `skman source remove <url>` 移除。

## 状态

所有状态都存在一个 JSON 文件:`~/.skman/state.json`。

```jsonc
{
  "version": 1,
  "sources": {
    "superpowers": { "type": "git", "url": "...", "ref": "main" }
  },
  "skills": {
    "brainstorming-ab12cd": {
      "slug": "brainstorming",
      "name": "brainstorming",
      "description": "You MUST use this before any creative work...",
      "source": "superpowers",
      "path": "skills/brainstorming",
      "commit": "a1b2c3d",
      "installed_at": "2026-05-14T10:00:00+00:00",
      "updated_at": "2026-05-14T12:00:00+00:00",
      "enabled": true
    }
  }
}
```

map key(`brainstorming-ab12cd`)也是目标目录下的符号链接名称。
`-ab12cd` 后缀是源 URL 的 6 位哈希,使两个源可以共用同一个 slug
而不冲突。

`skman list` 把状态渲染为表格:

```
LINK NAME             SLUG           SOURCE       COMMIT   STATUS    INSTALLED         UPDATED
brainstorming-ab12cd  brainstorming  superpowers  a1b2c3d  enabled   2026-05-14 10:00  2026-05-14 12:00
tdd-ab12cd            tdd            superpowers  a1b2c3d  enabled   2026-05-14 10:00  2026-05-14 12:00
```

## 重复检测

每次同步之后,skman 会按 SKILL.md frontmatter 中的 `(name, description)`
对 skill 分组,只要某对值在多个状态条目中出现,就会打印一条警告 ——
比如两个源都提供了 frontmatter 完全相同的 `brainstorming` skill。

警告只是提示信息:两个 skill 都仍然保留。符号链接名称包含从源 URL
派生出的短 id(`brainstorming-ab12cd`、`brainstorming-ef34gh`),
文件系统层面不会冲突。要彻底解决,可以删除其中一个源,或者用
`skman disable <link-name>` 禁用其中一个。

## 统计

`skman install-hook --write` 会为 Claude Code 和 Codex 注册
`PreToolUse` 钩子(仅当对应 Agent 的配置目录存在时)。缺少某个
Agent 的配置目录时会提示并跳过,所以只安装了其中一个 Agent 的机器上
也可以安全运行。每次 Skill 工具调用都会记录到
`~/.skman/stats/usage.jsonl`。
`skman stats` 进行聚合:

- 每个 skill 的调用次数、独立会话数、最近一次使用时间
- 在窗口期内未被使用的已管理 skill 数量

```bash
skman stats                    # 最近 30 天
skman stats --days 7
skman stats --skill brainstorming
```

## 从其它工具迁移

如果你之前用过 Claude Code、Codex 或 `skills.sh`(`npx skills …`),
你的 skill 可能散落在这些目录里:

- `~/.claude/skills/*` —— Claude Code 个人 skill
- `~/.codex/skills/*` —— Codex 个人 skill(`.system/` 会被跳过 ——
  那是 Codex 内置 skill 的位置)
- `~/.agents/skills/*` —— 跨 Agent 通用目录;`skills.sh` 也安装到这里

上述目录中的符号链接条目会被跳过 —— 只迁移真实的 skill 目录。这样
可以避免重复迁移跨 Agent 的符号链接(例如
`~/.codex/skills/foo → ~/.claude/skills/foo`);真实拷贝会在它实际
所在的位置被识别。

`skman migrate` 会遍历上述位置,查找尚未被 skman 管理的 `SKILL.md`
目录,并将其纳入管理:

- 如果存在 `~/.agents/.skill-lock.json`(skills.sh v3),读取其中
  记录的 `sourceUrl` —— 你通过 `npx skills` 安装的 skill 会变成
  skman 管理的 git source,共用同一仓库的 skill 会自动去重。
- 否则,如果该 skill 位于某个 git checkout 内,则使用该仓库的
  `origin` 注册为 git source。
- 否则,把 skill 复制到 `~/.skman/imported/<name>/`,注册为本地源。

`skman migrate` 不会覆盖你可能在本地编辑过的 skill:

- **在 git checkout 中** 有未提交修改或未推送 commit 时 —— 跳过。
  提交并推送到上游后重新运行。
- **在 `~/.agents/skills/` 中且 `.skill-lock.json` 记录了
  `skillFolderHash`**(skills.sh v3) —— 重新计算本地文件夹的
  git tree SHA-1,与记录值比较。不一致说明你在安装后编辑过该
  文件夹,skman 会跳过。(过滤 `.DS_Store`、`__pycache__`、`.git`、
  `node_modules` 等以避免误报。)

两种情况下,skman 都会明确告诉你是哪个 skill、在哪里、为什么跳过 ——
然后不去动它。手动处理(提交并推送,或还原本地修改,或干脆不用
skman 管理它)之后再重新运行。

迁移完成后,skman 会通过自己的带后缀的符号链接
(`brainstorming-ab12cd`)管理该 skill,并移除原来的散落拷贝,
避免宿主 Agent 同时看到两份。

```bash
skman migrate --dry-run            # 预览将会发生什么
skman migrate                      # 交互式(询问确认)
skman migrate --yes                # 非交互式
skman migrate --keep-originals     # 导入后保留磁盘原始拷贝
skman migrate --scan ~/elsewhere   # 额外扫描其它目录(可重复)
```

`skman setup` 等价于 `install-hook --write` 后接 `migrate`,是首次
运行的推荐命令。

## 命令一览

```
skman paths
skman setup      [--yes] [--keep-originals]
skman migrate    [--dry-run] [--yes] [--keep-originals] [--scan PATH]
skman source     add <url> [skills-to-enable] | remove <slug-or-url> | list
skman sync       [--source NAME | --skill SLUG]
skman list
skman refresh
skman show       <skill> [-a | --all]
skman enable     <skill>
skman disable    <skill>
skman stats      [--days N] [--skill SLUG]
skman hook
skman install-hook [--agent claude|codex|all] [--write]
```

`skills-to-enable` 是一个可选的、用逗号分隔的 skill slug 白名单。
设置后,同步完成后只启用列出的 skill;其余仍记录到状态中,但保持
禁用(不创建符号链接)。例如:

```bash
skman source add https://github.com/obra/superpowers.git              # 启用该源中所有 skill
skman source add https://github.com/obra/superpowers.git brainstorming,tdd
                                                                      # 只启用这两个;其余保持禁用
```

### 环境变量(进阶)

- `SKMAN_ROOT` —— 状态目录(默认 `~/.skman`)
- `SKMAN_TARGET_DIRS` —— 以冒号分隔的 Agent skill 目录列表
  (默认 `~/.agents/skills:~/.claude/skills`),主要供测试使用。
- `SKMAN_GITHUB_MIRROR` —— 通过镜像重写 GitHub 克隆 URL(在
  `github.com` 缓慢或被屏蔽的地区有用)。两种形式:
    - **主机名**(如 `hub.fastgit.org`)—— 替换 URL 中的
      `github.com`。`git@github.com:o/r` 会先转为 HTTPS,因此
      SSH 来源也能生效。
    - **完整 URL**(如 `https://ghproxy.com`)—— 视为前缀;
      原始 `https://github.com/o/r` URL 会被附加在后面。
  `state.json` 中记录的原始 `url` 不会改变;镜像只在 clone/fetch
  时生效,sync 会打印重写后的 URL。

## 发布(给维护者)

版本号从 `skman/__init__.py` 中的 `__version__` 读取。

### 通过 GitHub Actions(推荐)

修改 `skman/__init__.py` 中的 `__version__`,提交并 push。然后打开
**Actions → Publish to PyPI → Run workflow**,输入刚设置的版本号。
该 workflow 会先跑测试、构建 sdist/wheel、校验版本号是否一致,
再通过 [trusted publishing](https://docs.pypi.org/trusted-publishers/)
发布到 PyPI(无需存储 token)。

一次性设置:在 PyPI 项目的 *Publishing* 设置中添加一个 trusted
publisher,owner 填 `zhendong`,repository 填 `skill-man`,workflow
填 `publish.yml`,environment 填 `pypi`。

### 手动发布

```bash
# 1. 修改 skman/__init__.py 中的 __version__ 并提交
# 2. 打 tag(可选但推荐)
git tag v$(python3 -c "import skman; print(skman.__version__)")
git push --tags

# 3. 构建
python3 -m pip install --upgrade build twine
rm -rf dist/ && python3 -m build           # 产物:dist/skman-X.Y.Z-py3-none-any.whl 和 .tar.gz

# 4. 检查产物
python3 -m twine check dist/*

# 5. 先上传 TestPyPI,再上传 PyPI
python3 -m twine upload --repository testpypi dist/*
python3 -m twine upload dist/*
```

凭据配置在 `~/.pypirc` 中,或通过 API token 设置环境变量:
`TWINE_USERNAME=__token__ TWINE_PASSWORD=<pypi-token>`。

## License

[MIT](LICENSE)。
