# AstrBot 插件 UI 开发与验收规范

**版本**：1.0  
**最后更新**：2026-09-21  
**适用插件**：astrbot_plugin_proxy_manage（代理管理中心）

---

## 一、问题根源总结

### 1.1 本次（0.3.16）修复的核心问题

#### 问题1：布局CSS缺失导致页面完全空白
**现象**：
- 插件页面主内容区域完全空白
- 只显示AstrBot主应用框架，插件内容不可见
- DOM元素（`.layout`、`.sidebar`、`.main-content`）完全不存在

**根本原因**：
- `index.html` 缺少核心布局CSS定义
- 缺失的CSS类：`.layout`、`.sidebar`、`.main-content`、`.top-bar` 等
- 没有定义Grid布局容器的基础样式

**修复方案**（提交 `8337ca4`）：
```css
/* 必须包含的核心布局CSS */
.layout {
    display: grid;
    grid-template-columns: 200px 1fr;
    height: 100%;
    gap: 0;
}

.sidebar {
    background: #f8f9fa;
    border-right: 1px solid #dee2e6;
    padding: 1rem 0;
    overflow-y: auto;
}

.main-content {
    padding: 1.5rem;
    overflow-y: auto;
    background: white;
}

.top-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 2px solid #e9ecef;
}
```

#### 问题2：导航按钮样式缺失
**现象**：
- 侧边栏按钮没有正确的样式
- 按钮点击状态不明显

**根本原因**：
- 缺少 `button[data-tab]` 的专用CSS规则

**修复方案**（提交 `f78f20f`）：
```css
button[data-tab] {
    width: 100%;
    padding: 0.75rem 1.5rem;
    text-align: left;
    background: transparent;
    border: none;
    cursor: pointer;
    transition: all 0.2s;
    font-size: 0.95rem;
}

button[data-tab].active {
    background: #e3f2fd;
    border-left: 3px solid #2196F3;
    font-weight: 500;
}
```

#### 问题3：主容器宽度限制问题
**现象**：
- 内容区域宽度受限，显示不完整

**修复方案**（提交 `058452b`）：
```css
.main-content {
    max-width: none; /* 移除宽度限制 */
    width: 100%;
}
```

### 1.2 Docker部署的关键点

**重要**：修改HTML文件后，必须重启Docker容器才能生效！

```bash
# 部署后必须执行
sudo docker restart astrbot-astrbot-1
```

**原因**：
- AstrBot在容器启动时加载插件HTML
- 热部署只更新文件，不会重新加载HTML模板
- 必须重启容器才能使新的HTML生效

---

## 二、UI开发强制清单

### 2.1 必须包含的CSS定义

每个AstrBot插件页面必须包含以下CSS：

#### 基础布局（必需）
```css
/* 1. 主布局容器 */
.layout {
    display: grid;
    grid-template-columns: 200px 1fr;
    height: 100%;
    gap: 0;
}

/* 2. 侧边栏 */
.sidebar {
    background: #f8f9fa;
    border-right: 1px solid #dee2e6;
    padding: 1rem 0;
    overflow-y: auto;
}

/* 3. 主内容区 */
.main-content {
    padding: 1.5rem;
    overflow-y: auto;
    background: white;
    max-width: none;
    width: 100%;
}

/* 4. 顶部工具栏 */
.top-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 2px solid #e9ecef;
}
```

#### 导航按钮（必需）
```css
/* 侧边栏导航按钮 */
button[data-tab] {
    width: 100%;
    padding: 0.75rem 1.5rem;
    text-align: left;
    background: transparent;
    border: none;
    cursor: pointer;
    transition: all 0.2s;
    font-size: 0.95rem;
    color: #495057;
}

button[data-tab]:hover {
    background: #f1f3f5;
}

button[data-tab].active {
    background: #e3f2fd;
    border-left: 3px solid #2196F3;
    font-weight: 500;
    color: #1976D2;
}
```

#### 表单元素（推荐）
```css
/* 输入框 */
input[type="text"],
input[type="number"],
textarea,
select {
    padding: 0.5rem;
    border: 1px solid #ced4da;
    border-radius: 4px;
    font-size: 0.95rem;
}

input[type="text"]:focus,
textarea:focus,
select:focus {
    outline: none;
    border-color: #2196F3;
    box-shadow: 0 0 0 0.2rem rgba(33, 150, 243, 0.25);
}

/* 复选框容器（Grid布局） */
.checkbox-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.75rem;
    margin: 1rem 0;
}

.checkbox-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem;
    background: #f8f9fa;
    border-radius: 4px;
}

/* 按钮 */
button {
    padding: 0.5rem 1rem;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.95rem;
    transition: all 0.2s;
}

button:hover {
    opacity: 0.9;
}
```

### 2.2 HTML结构模板

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>插件名称</title>
    <style>
        /* 1. 重置样式 */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            line-height: 1.6;
        }

        /* 2. 布局CSS（必需） */
        .layout { /* ... */ }
        .sidebar { /* ... */ }
        .main-content { /* ... */ }
        .top-bar { /* ... */ }

        /* 3. 导航按钮CSS（必需） */
        button[data-tab] { /* ... */ }

        /* 4. 表单元素CSS（根据需要） */
        /* ... */
    </style>
</head>
<body>
    <div class="layout">
        <!-- 侧边栏 -->
        <nav class="sidebar">
            <button data-tab="overview" class="active">概览</button>
            <button data-tab="config">配置</button>
            <!-- 更多导航项 -->
        </nav>

        <!-- 主内容区 -->
        <main class="main-content">
            <!-- 顶部工具栏 -->
            <div class="top-bar">
                <h2 id="page-title">概览</h2>
                <div>
                    <button>操作按钮</button>
                </div>
            </div>

            <!-- 页面内容 -->
            <div id="content-area">
                <!-- 动态内容 -->
            </div>
        </main>
    </div>

    <script>
        // JavaScript逻辑
    </script>
</body>
</html>
```

---

## 三、验收流程（强制执行）

### 3.1 开发阶段检查清单

**在提交代码前**，必须完成以下检查：

- [ ] 所有必需的CSS类都已定义（`.layout`、`.sidebar`、`.main-content`、`.top-bar`）
- [ ] 导航按钮样式完整（`button[data-tab]` 及其 `.active` 状态）
- [ ] 表单元素样式一致（输入框、下拉框、复选框、按钮）
- [ ] 多选框使用Grid布局，不是垂直堆叠
- [ ] 所有按钮都有明确的hover和active状态
- [ ] 没有内联样式（inline style），所有样式都在`<style>`或独立CSS文件中
- [ ] 没有CSS重复定义（检查是否有多处定义同一个类）

### 3.2 本地部署检查

```bash
# 1. 提交代码
cd /Users/kingboat/Documents/astrbot_plugin_proxy_manage
git add -A
git commit -m "功能说明 (0.3.16)"
git push origin develop

# 2. 部署到服务器
cd /Users/kingboat/Documents/server-operations/43.138.195.178
./deploy-proxy-manager.sh hot <提交SHA>

# 3. 重启Docker容器（关键！）
ssh 43.138.195.178
sudo docker restart astrbot-astrbot-1

# 4. 等待容器重启（30-40秒）
sudo docker ps | grep astrbot

# 5. 检查插件加载状态
sudo docker logs --tail 50 astrbot-astrbot-1 | grep proxy_manage
```

### 3.3 浏览器验收（强制）

**必须登录实际网站进行验收**，不能只看代码或本地文件！

#### 验收步骤：
1. 打开 `https://astrbot.gobelievehub.top`
2. 登录（账号：astrbot，密码：Ab9LM6iYTuKeLY7gYd_mXK3TVvyr8cPquFG）
3. 导航到插件页面：`/#/plugin-page/astrbot_plugin_proxy_manage/manage`
4. 检查每个页面

#### 验收清单（每个页面）：

**概览页面**
- [ ] 左侧菜单完整显示
- [ ] 主内容区显示正常（不是空白）
- [ ] 所有卡片和信息正常显示

**订阅管理页面**
- [ ] 表单输入框对齐整齐
- [ ] 多行文本框高度合适
- [ ] 按钮水平排列（如适用）
- [ ] 已配置订阅列表显示正常

**代理节点页面**
- [ ] 筛选下拉框水平排列
- [ ] 节点卡片显示正常
- [ ] 复选框和输入框对齐

**代理组页面**（重点）
- [ ] **多选框以Grid布局显示（3列或合适的列数）**
- [ ] 每个复选框都有对应的标签
- [ ] 复选框对齐整齐，间距合理
- [ ] 滚动时布局不会错乱

**分流规则页面**
- [ ] 输入框和下拉框对齐
- [ ] 多行文本框高度合适
- [ ] 按钮显示正常

**平台域名模板页面**
- [ ] 模板卡片显示正常
- [ ] 下拉框和按钮显示正常

**内核管理页面**
- [ ] 状态标签显示正常
- [ ] 按钮组水平排列
- [ ] 状态信息卡片显示正常

**连接日志页面**
- [ ] 日志列表显示正常
- [ ] 时间戳和内容对齐

### 3.4 使用Playwright自动化验收（推荐）

创建验收脚本以确保每次都检查：

```javascript
// 在Codex中使用 mcp__cua_repl__js 工具
let tab = await cua.createBrowserTab("iab", "https://astrbot.gobelievehub.top/#/plugin-page/astrbot_plugin_proxy_manage/manage", {visible: true});

// 等待页面加载
await tab.playwright.waitForTimeout(3000);

// 在iframe中操作
let frame = tab.playwright.frameLocator('iframe');

// 检查核心元素是否存在
let layoutExists = await frame.locator('.layout').count() > 0;
let sidebarExists = await frame.locator('.sidebar').count() > 0;
let mainContentExists = await frame.locator('.main-content').count() > 0;

if (!layoutExists || !sidebarExists || !mainContentExists) {
    throw new Error('核心布局元素缺失！');
}

// 截图验证
let screenshot = await tab.screenshot();
await nodeRepl.emitImage(screenshot);
```

---

## 四、常见问题与解决方案

### 4.1 页面完全空白

**症状**：主内容区完全空白，只显示AstrBot主应用框架

**排查步骤**：
1. 检查 `index.html` 是否包含 `.layout`、`.sidebar`、`.main-content` 的CSS定义
2. 检查是否重启了Docker容器
3. 使用浏览器开发者工具检查DOM结构，确认元素是否存在

**解决方案**：
- 补充缺失的CSS定义
- 重启Docker容器：`sudo docker restart astrbot-astrbot-1`

### 4.2 多选框显示混乱

**症状**：复选框垂直堆叠或对齐不整齐

**解决方案**：
```css
.checkbox-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr); /* 或 repeat(auto-fill, minmax(200px, 1fr)) */
    gap: 0.75rem;
}
```

### 4.3 按钮样式不一致

**症状**：某些按钮有样式，某些按钮没有

**解决方案**：
- 确保所有按钮都有基础按钮样式
- 使用统一的按钮类或data属性
- 避免CSS选择器优先级冲突

### 4.4 输入框对齐问题

**症状**：输入框高度不一致，标签和输入框不对齐

**解决方案**：
```css
.form-row {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1rem;
}

.form-row label {
    min-width: 100px;
}

.form-row input,
.form-row select {
    flex: 1;
    padding: 0.5rem;
}
```

### 4.5 Docker部署后页面未更新

**症状**：代码已部署，但页面显示还是旧的

**原因**：
- 忘记重启Docker容器
- 浏览器缓存

**解决方案**：
1. 重启Docker容器：`sudo docker restart astrbot-astrbot-1`
2. 清除浏览器缓存或硬刷新（Ctrl+Shift+R）
3. 检查服务器文件是否真的更新了：`cat /srv/apps/astrbot/state/data/plugins/astrbot_plugin_proxy_manage/pages/manage/index.html | head -20`

---

## 五、CSS组织最佳实践

### 5.1 CSS分层结构

```css
/* ========== 1. 重置样式 ========== */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

/* ========== 2. 全局样式 ========== */
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    line-height: 1.6;
}

/* ========== 3. 布局样式（核心） ========== */
.layout { /* ... */ }
.sidebar { /* ... */ }
.main-content { /* ... */ }
.top-bar { /* ... */ }

/* ========== 4. 组件样式 ========== */
/* 导航按钮 */
button[data-tab] { /* ... */ }

/* 卡片 */
.card { /* ... */ }

/* 表单 */
.form-row { /* ... */ }

/* ========== 5. 工具类 ========== */
.text-muted { color: #6c757d; }
.text-danger { color: #dc3545; }
.mb-1 { margin-bottom: 0.5rem; }
```

### 5.2 避免CSS冲突

**不要这样做**：
```css
/* 错误：重复定义 */
.sidebar {
    background: #f8f9fa;
}

/* ... 500行后 ... */

.sidebar {
    background: white; /* 覆盖了之前的定义 */
}
```

**应该这样做**：
```css
/* 正确：一次性完整定义 */
.sidebar {
    background: #f8f9fa;
    border-right: 1px solid #dee2e6;
    padding: 1rem 0;
    overflow-y: auto;
}
```

### 5.3 使用CSS变量（推荐）

```css
:root {
    --primary-color: #2196F3;
    --sidebar-bg: #f8f9fa;
    --border-color: #dee2e6;
    --text-color: #495057;
    --spacing: 1rem;
}

.sidebar {
    background: var(--sidebar-bg);
    border-right: 1px solid var(--border-color);
    padding: var(--spacing) 0;
}

button[data-tab].active {
    border-left-color: var(--primary-color);
}
```

---

## 六、版本历史与教训

### 0.3.16（2026-09-21）

**问题**：
- 页面完全空白
- 布局CSS缺失
- 导航按钮样式缺失

**修复提交**：
- `8337ca4` - 补充缺失的布局CSS定义
- `f78f20f` - 修复导航按钮样式
- `e681861` - 增强侧边栏激活状态对比度
- `058452b` - 修复主容器宽度限制问题

**教训**：
1. **必须在实际网站验收**，不能只看代码
2. **修改HTML后必须重启Docker容器**
3. **核心布局CSS必须包含在HTML中**，不能依赖外部CSS文件
4. **逐页检查所有UI元素**，包括多选框、输入框、按钮等
5. **验收必须彻底**，不能只看第一页就认为没问题

---

## 七、开发者自检表

**在每次提交前，开发者必须回答以下问题**：

- [ ] 我是否已经在 `index.html` 中包含了所有必需的布局CSS？
- [ ] 我是否已经定义了 `.layout`、`.sidebar`、`.main-content`、`.top-bar` 这些核心类？
- [ ] 我是否已经为 `button[data-tab]` 定义了样式和 `.active` 状态？
- [ ] 我是否已经检查了所有表单元素（输入框、下拉框、复选框、按钮）的样式？
- [ ] 我是否已经使用Grid布局来组织多选框？
- [ ] 我是否已经部署到服务器并重启了Docker容器？
- [ ] 我是否已经登录实际网站进行了验收？
- [ ] 我是否已经检查了所有页面（概览、订阅管理、代理节点、代理组、分流规则、平台域名模板、内核管理、连接日志）？
- [ ] 我是否已经截图保存了验收结果？
- [ ] 我是否已经在Git提交信息中正确标注了版本号？

**如果任何一个问题的答案是"否"，请不要提交代码！**

---

## 八、快速参考

### 8.1 部署命令

```bash
# 1. 本地提交
cd /Users/kingboat/Documents/astrbot_plugin_proxy_manage
git add -A
git commit -m "说明 (版本号)"
git push origin develop

# 2. 部署到服务器
cd /Users/kingboat/Documents/server-operations/43.138.195.178
./deploy-proxy-manager.sh hot <SHA>

# 3. 重启容器
ssh 43.138.195.178
sudo docker restart astrbot-astrbot-1

# 4. 检查状态
sudo docker ps | grep astrbot
sudo docker logs --tail 50 astrbot-astrbot-1 | grep proxy_manage
```

### 8.2 验收URL

- 网站：https://astrbot.gobelievehub.top
- 插件页面：`/#/plugin-page/astrbot_plugin_proxy_manage/manage`
- 账号：astrbot
- 密码：Ab9LM6iYTuKeLY7gYd_mXK3TVvyr8cPquFG

### 8.3 核心CSS检查

```bash
# 检查CSS是否包含核心类
grep -E "\.layout|\.sidebar|\.main-content|\.top-bar|button\[data-tab\]" pages/manage/index.html
```

---

**本文档必须在每次UI修复后更新，记录新的问题和解决方案。**
