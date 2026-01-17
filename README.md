# AI内容生成记录系统

一个基于Flask的HTTP服务器，用于接收和管理AI内容生成记录信息，支持案例画廊展示和持久化存储。

## 功能特性

- **案例画廊**: 首页展示所有AI创作案例，支持卡片式浏览
- **无限滚动**: 向下滚动自动分批加载更多案例，避免单次加载过大
- **应用筛选**: 按app_id筛选和导航不同应用的案例
- **表单提交**: 通过HTTP表单提交AI内容生成记录
- **文件管理**: 支持上传素材文件和生成的结果文件
- **智能分类**: 自动识别文件类型（文本、图片、视频）
- **详情展示**: 生成现代化的HTML详情页面
- **数据持久化**: JSON格式存储所有记录数据
- **扁平化UI**: Windows 11风格的黑白灰简约设计

## 支持的文件类型

### 文本文件
- .txt, .md, .csv, .json, .xml

### 图片文件
- .jpg, .jpeg, .png, .gif, .bmp, .webp, .svg

### 视频文件
- .mp4, .avi, .mov, .mkv, .webm, .flv

## 安装步骤

1. 安装Python依赖：
```bash
pip install -r requirements.txt
```

2. 运行程序：
```bash
python app.py
```

3. 在浏览器中访问：
```
http://localhost:5000       # 案例画廊首页
http://localhost:5000/form  # 提交新案例
```

## 项目结构

```
demo_site/
├── app.py              # Flask主程序
├── requirements.txt    # Python依赖
├── templates/          # HTML模板文件夹
│   ├── gallery.html   # 案例画廊首页
│   ├── form.html      # 表单提交页面
│   └── display.html   # 内容展示页面
├── data/              # 数据存储目录
│   ├── index.json     # 记录索引文件（轻量级）
│   ├── records/        # 按app_id分类的记录文件
│   │   ├── stable_diffusion/
│   │   │   ├── 20260118...json
│   │   │   └── 20260118...json
│   │   ├── midjourney/
│   │   │   └── 20260118...json
│   │   └── ...
│   └── records.json.backup  # 旧数据备份（如有）
├── uploads/           # 上传的素材文件存储目录
├── generated/         # 生成的结果文件存储目录
├── thumbnails/        # 视频缩略图存储目录
└── output/            # 生成的HTML页面输出目录
```

## 数据存储架构

为支持大数据量场景，系统采用**分文件存储 + 索引 + 按app_id分类**的架构：

### 存储结构

1. **记录文件** (`data/records/{app_id}/{id}.json`)
   - 每个表单提交对应一个独立的JSON文件
   - 按app_id分类存储在不同子目录
   - 文件名为记录的唯一ID
   - 包含完整的记录数据（参数、文件信息等）

2. **索引文件** (`data/index.json`)
   - 轻量级索引，只包含元信息
   - 用于快速检索和分页
   - 避免加载所有完整记录

3. **目录组织**
   - 不同应用的记录分开存储
   - 便于管理和备份特定应用的数据
   - 支持应用级别的数据隔离

### 索引结构示例

```json
{
  "records": [
    {
      "id": "20260118223456789012",
      "created_at": "2026-01-18T22:34:56.789012",
      "title": "AI生成的猫咪图片",
      "app_id": "stablediffusion_v1",
      "generation_time": "2026-01-18T22:30:00",
      "html_file": "record_20260118_223456.html",
      "has_preview": true,
      "preview_type": "image"
    }
  ],
  "updated_at": "2026-01-18T22:34:56.789012",
  "total_count": 100
}
```

### 完整记录结构

`data/records/20260118223456789012.json`:

```json
{
  "id": "20260118223456789012",
  "created_at": "2026-01-18T22:34:56.789012",
  "title": "AI生成的猫咪图片",
  "app_id": "stablediffension_v1",
  "generation_time": "2026-01-18T22:30:00",

  "parameters": {
    "prompt": "a cute cat in a garden",
    "negative_prompt": "blurry, low quality",
    "resolution": "1024x1024",
    "seed": 42,
    "steps": 30,
    "cfg_scale": 7.5,
    "sampler": "DPM++ 2M Karras",
    "model": "sd_xl_base_1.0",
    "custom_params": {
      "style": "photorealistic"
    }
  },

  "files": {
    "materials": [...],
    "results": [...]
  },

  "statistics": {
    "material_count": 1,
    "result_count": 1,
    "total_size": 358023
  },

  "html_file": "record_20260118_223456.html"
}
```

### 性能优势

- **快速加载**：列表页只加载索引，不加载完整数据
- **按需读取**：点击详情时才加载完整记录
- **应用隔离**：不同应用的数据独立存储，互不影响
- **并发友好**：不同应用的数据独立存储，支持并发读写
- **易于扩展**：单个文件损坏不影响其他记录
- **便于管理**：可以单独备份或删除特定应用的所有数据
- **自动迁移**：系统会自动将旧的单文件格式迁移到新格式

### 参数信息输入格式

系统支持智能解析参数信息，可以输入：

**1. 结构化格式（推荐）**：
```
提示词: 一只可爱的猫咪在花园里玩耍
负向提示词: 模糊, 低质量
分辨率: 1024x1024
随机种子: 42
采样步数: 30
CFG Scale: 7.5
采样器: DPM++ 2M Karras
模型: SDXL 1.0
风格: 写实风格
```

**2. 纯文本格式**：
```
一只可爱的猫咪在花园里玩耍，高清，高质量
```

系统会自动识别 `key: value` 格式的参数，并将其结构化存储。

## 使用说明

### 1. 查看案例画廊
访问首页 http://localhost:5000 可以：
- 浏览所有AI创作案例
- 点击app_id筛选特定应用的案例
- 滚动页面自动加载更多案例
- 点击卡片查看详情

### 2. 提交新案例
访问 http://localhost:5000/form 填写表单：
- 内容标题（必填）
- **应用ID（必填，用于分类和数据隔离）**
- 生成日期时间（必填）
- 参数信息（必填，可包含提示词、分辨率、随机种子等推理参数）
- 素材文件（可选，支持多文件上传）
- 生成的结果文件（可选，支持多文件上传）

**应用ID说明**：
- 应用ID用于标识不同的AI生成工具（如：stable_diffusion, midjourney, dalle等）
- 相同应用ID的记录会存储在同一目录下
- 首页可以按应用ID筛选和浏览案例

### 3. 查看案例详情
点击案例卡片进入详情页，包含：
- 标题、时间、app_id信息
- 统计信息（文件数量）
- 参数信息（提示词、分辨率、随机种子等）
- 素材文件预览（图片、视频）
- 生成结果预览（图片、视频）

## API接口

### 获取记录列表
```
GET /api/records?page=1&per_page=12&app_id=
```
- page: 页码（默认1）
- per_page: 每页数量（默认12）
- app_id: 应用ID筛选（可选）

### 获取应用列表
```
GET /api/apps
```
返回所有不重复的app_id列表

## 技术栈

- **后端**: Flask (Python)
- **前端**: HTML5 + CSS3 + JavaScript
- **数据存储**: JSON文件
- **视频处理**: OpenCV (可选)
- **设计**: Windows 11风格，黑白灰配色

## 特性说明

- **智能预览系统**
  - 图像文件：直接显示缩略图
  - 视频文件：自动提取首帧作为缩略图（带播放指示器）
  - 文本文件：提取前100个字符作为预览内容
- 案例卡片展示（封面图、标题、时间、app_id）
- 无限滚动分批加载（每页12条）
- 按app_id分类筛选
- 文件类型自动检测和分类
- 图片和视频文件预览
- 统计信息展示
- 移动端响应式适配
- 简约扁平化设计
- 悬停动画效果
- 表单验证
- 拖拽上传支持

## 注意事项

- 最大文件上传限制：2GB
- **数据存储**：
  - 每个记录保存在独立的JSON文件：`data/records/{app_id}/{id}.json`
  - 按应用ID分类存储，便于管理和备份
  - 索引文件：`data/index.json`（快速检索）
  - 旧数据会自动迁移到新格式并备份为 `data/records.json.backup`
- **必填字段**：内容标题、应用ID、生成日期时间、参数信息
- 生成的HTML文件保存在 `output/` 目录
- 上传的文件保存在 `uploads/` 和 `generated/` 目录
- 视频缩略图保存在 `thumbnails/` 目录
- 如需调整文件大小限制，请修改app.py中的MAX_CONTENT_LENGTH配置
- 如需调整每页加载数量，请修改gallery.html中的perPage变量
- **视频缩略图功能需要安装OpenCV**: `pip install opencv-python`
- 如果未安装OpenCV，视频文件将只显示占位图标，不影响其他功能
