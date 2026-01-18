from flask import Flask, request, render_template, jsonify, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
import os
import json
from datetime import datetime
from werkzeug.utils import secure_filename
import mimetypes
import uuid

try:
    import cv2
    CV2_AVAILABLE = True
    print(f"OpenCV导入成功，版本: {cv2.__version__}")
except ImportError as e:
    CV2_AVAILABLE = False
    print(f"警告: OpenCV导入失败: {e}")
    print("视频缩略图功能将被禁用。")
    print("诊断信息:")
    import sys
    print(f"Python版本: {sys.version}")
    try:
        import importlib.util
        spec = importlib.util.find_spec("cv2")
        if spec:
            print(f"cv2模块位置: {spec.origin}")
        else:
            print("cv2模块未找到")
    except Exception as e2:
        print(f"查找cv2模块失败: {e2}")

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['GENERATED_FOLDER'] = 'generated'
app.config['OUTPUT_FOLDER'] = 'output'
app.config['DATA_FOLDER'] = 'data'
app.config['THUMBNAIL_FOLDER'] = 'thumbnails'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max file size

# 确保必要的文件夹存在
for folder in [app.config['UPLOAD_FOLDER'], app.config['GENERATED_FOLDER'],
               app.config['OUTPUT_FOLDER'], app.config['DATA_FOLDER'],
               app.config['THUMBNAIL_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

DATA_FILE = os.path.join(app.config['DATA_FOLDER'], 'records.json')
INDEX_FILE = os.path.join(app.config['DATA_FOLDER'], 'index.json')
RECORDS_DIR = os.path.join(app.config['DATA_FOLDER'], 'records')

# 确保记录目录存在
os.makedirs(RECORDS_DIR, exist_ok=True)

# 允许的文件类型
ALLOWED_EXTENSIONS = {
    'text': ['.txt', '.md', '.csv', '.json', '.xml'],
    'image': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'],
    'video': ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv']
}

def get_file_category(filename):
    """判断文件类型分类"""
    ext = os.path.splitext(filename)[1].lower()
    for category, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return category
    return 'unknown'

def allowed_file(filename):
    """检查文件类型是否允许"""
    ext = os.path.splitext(filename)[1].lower()
    all_extensions = [ext for extensions in ALLOWED_EXTENSIONS.values() for ext in extensions]
    return ext in all_extensions

def load_records():
    """加载记录索引（轻量级）"""
    # 优先使用新的索引文件
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            index_data = json.load(f)
            return index_data.get('records', [])

    # 兼容旧的单文件模式
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            old_records = json.load(f)
            # 迁移到新格式
            migrate_to_index(old_records)
            return old_records

    return []

def save_records(records):
    """保存记录索引"""
    index_data = {
        'records': records,
        'updated_at': datetime.now().isoformat(),
        'total_count': len(records)
    }
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

def load_record(record_id, app_id):
    """加载单个完整记录"""
    app_dir = os.path.join(RECORDS_DIR, app_id)
    record_file = os.path.join(app_dir, f"{record_id}.json")
    if os.path.exists(record_file):
        with open(record_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_record(record):
    """保存单个记录到app_id对应的子目录"""
    app_id = record.get('app_id', 'default')
    app_dir = os.path.join(RECORDS_DIR, app_id)
    os.makedirs(app_dir, exist_ok=True)

    record_file = os.path.join(app_dir, f"{record['id']}.json")
    with open(record_file, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record

def migrate_to_index(old_records):
    """将旧的单文件数据迁移到新的分文件格式"""
    print("正在迁移数据到新的分文件格式...")

    for record in old_records:
        # 保存完整记录到独立文件
        save_record(record)

    # 创建索引
    index_records = []
    for record in old_records:
        # 创建轻量级索引条目
        index_entry = {
            'id': record['id'],
            'created_at': record['created_at'],
            'title': record['title'],
            'app_id': record.get('app_id'),
            'generation_time': record['generation_time'],
            'html_file': record.get('html_file'),
            'has_preview': bool(get_main_preview(record)),
            'preview_type': get_main_preview(record)['type'] if get_main_preview(record) else None
        }
        index_records.append(index_entry)

    # 保存索引
    save_records(index_records)

    # 备份旧文件
    if os.path.exists(DATA_FILE):
        backup_file = DATA_FILE + '.backup'
        os.rename(DATA_FILE, backup_file)
        print(f"旧数据已备份到: {backup_file}")

    print(f"已迁移 {len(old_records)} 条记录到新格式")

def parse_parameters(params_text):
    """解析参数信息文本，转换为结构化数据"""
    parameters = {
        'prompt': '',
        'custom_params': {}
    }

    lines = params_text.strip().split('\n')
    current_field = 'prompt'

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 检测是否为 key: value 格式
        if ':' in line and not line.startswith('http'):
            parts = line.split(':', 1)
            key = parts[0].strip().lower()
            value = parts[1].strip() if len(parts) > 1 else ''

            # 映射常见参数名
            key_mapping = {
                '提示词': 'prompt',
                'prompt': 'prompt',
                '负向提示词': 'negative_prompt',
                'negative_prompt': 'negative_prompt',
                'negative': 'negative_prompt',
                '分辨率': 'resolution',
                'resolution': 'resolution',
                'size': 'resolution',
                '随机种子': 'seed',
                'seed': 'seed',
                '采样步数': 'steps',
                'steps': 'steps',
                'cfg': 'cfg_scale',
                'cfg_scale': 'cfg_scale',
                '采样器': 'sampler',
                'sampler': 'sampler',
                '模型': 'model',
                'model': 'model',
            }

            mapped_key = key_mapping.get(key)
            if mapped_key:
                # 尝试转换数值类型
                if mapped_key in ['seed', 'steps', 'cfg_scale']:
                    try:
                        parameters[mapped_key] = int(value) if mapped_key in ['seed', 'steps'] else float(value)
                    except ValueError:
                        parameters[mapped_key] = value
                else:
                    parameters[mapped_key] = value
            else:
                # 未识别的参数放入custom_params
                parameters['custom_params'][key] = value
        else:
            # 普通文本，追加到prompt
            if parameters['prompt']:
                parameters['prompt'] += '\n' + line
            else:
                parameters['prompt'] = line

    return parameters

def format_parameters(parameters):
    """将结构化参数转换为文本格式"""
    if not parameters:
        return ''

    lines = []

    # 优先显示prompt
    if 'prompt' in parameters and parameters['prompt']:
        lines.append(f"提示词: {parameters['prompt']}")

    # 其他标准参数
    param_order = ['negative_prompt', 'resolution', 'seed', 'steps', 'cfg_scale', 'sampler', 'model']
    param_labels = {
        'negative_prompt': '负向提示词',
        'resolution': '分辨率',
        'seed': '随机种子',
        'steps': '采样步数',
        'cfg_scale': 'CFG Scale',
        'sampler': '采样器',
        'model': '模型'
    }

    for param in param_order:
        if param in parameters and parameters[param]:
            label = param_labels.get(param, param)
            lines.append(f"{label}: {parameters[param]}")

    # 自定义参数
    if 'custom_params' in parameters and parameters['custom_params']:
        for key, value in parameters['custom_params'].items():
            lines.append(f"{key}: {value}")

    return '\n'.join(lines)

def generate_video_thumbnail(video_path, filename):
    """从视频中提取第一帧作为缩略图"""
    print(f"[DEBUG] generate_video_thumbnail called: filename={filename}, path={video_path}")
    print(f"[DEBUG] CV2_AVAILABLE: {CV2_AVAILABLE}")

    if not CV2_AVAILABLE:
        print("[DEBUG] OpenCV not available, returning None")
        return None

    try:
        # 生成缩略图文件名
        thumbnail_name = f"thumb_{os.path.splitext(filename)[0]}.jpg"
        thumbnail_path = os.path.join(app.config['THUMBNAIL_FOLDER'], thumbnail_name)

        print(f"[DEBUG] Thumbnail name: {thumbnail_name}")
        print(f"[DEBUG] Thumbnail path: {thumbnail_path}")
        print(f"[DEBUG] Video file exists: {os.path.exists(video_path)}")

        # 如果缩略图已存在，直接返回
        if os.path.exists(thumbnail_path):
            print(f"[DEBUG] Thumbnail already exists, returning: /thumbnails/{thumbnail_name}")
            return f"/thumbnails/{thumbnail_name}"

        # 使用OpenCV读取视频第一帧
        print(f"[DEBUG] Attempting to read video with cv2.VideoCapture...")
        video = cv2.VideoCapture(video_path)
        print(f"[DEBUG] VideoCapture opened: {video.isOpened()}")

        success, frame = video.read()
        print(f"[DEBUG] Frame read success: {success}, frame shape: {frame.shape if success else 'N/A'}")

        if success:
            # 调整大小为宽度300px
            height, width = frame.shape[:2]
            new_width = 300
            new_height = int(height * (new_width / width))
            resized_frame = cv2.resize(frame, (new_width, new_height))

            # 保存为JPEG
            cv2.imwrite(thumbnail_path, resized_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            print(f"[DEBUG] Thumbnail saved to: {thumbnail_path}")
            print(f"[DEBUG] Thumbnail file exists after save: {os.path.exists(thumbnail_path)}")
            video.release()
            return f"/thumbnails/{thumbnail_name}"

        video.release()
        print(f"[DEBUG] Failed to read video frame, returning None")
        return None
    except Exception as e:
        print(f"[DEBUG] 生成视频缩略图失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def extract_text_preview(file_path):
    """从文本文件中提取前100个字符作为预览"""
    try:
        # 尝试不同编码读取
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']

        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    content = f.read(100)
                    return content
            except UnicodeDecodeError:
                continue

        return None
    except Exception as e:
        print(f"提取文本预览失败: {e}")
        return None

def generate_preview_info(file_info, folder_type):
    """为文件生成预览信息"""
    print(f"[DEBUG] generate_preview_info called: category={file_info['category']}, filename={file_info['filename']}")

    preview = {
        'type': file_info['category'],
        'filename': file_info['filename']
    }

    file_path = file_info['path']

    if file_info['category'] == 'image':
        # 图像直接使用文件路径
        preview['url'] = f"/{folder_type}/{file_info['filename']}"
        print(f"[DEBUG] Image preview: {preview['url']}")
    elif file_info['category'] == 'video':
        # 视频生成缩略图
        print(f"[DEBUG] Video file detected, calling generate_video_thumbnail...")
        print(f"[DEBUG] Video full_path: {file_info.get('full_path', 'N/A')}")
        thumbnail_url = generate_video_thumbnail(file_info['full_path'], file_info['filename'])
        print(f"[DEBUG] Video thumbnail result: {thumbnail_url}")
        if thumbnail_url:
            preview['thumbnail'] = thumbnail_url
        else:
            preview['thumbnail'] = None
    elif file_info['category'] == 'text':
        # 文本提取预览
        text_preview = extract_text_preview(file_path)
        preview['text'] = text_preview if text_preview else ''
        print(f"[DEBUG] Text preview length: {len(preview['text']) if preview['text'] else 0}")

    print(f"[DEBUG] Final preview object: {preview}")
    return preview

def get_cover_image(record):
    """获取记录的封面图片"""
    # 优先使用生成的结果图片
    for result in record.get('files', {}).get('results', []):
        if result['category'] == 'image':
            return result['path']
    # 如果没有结果图片，使用素材图片
    for material in record.get('files', {}).get('materials', []):
        if material['category'] == 'image':
            return material['path']
    return None

def get_main_preview(record):
    """获取记录的主预览信息"""
    print(f"[DEBUG] get_main_preview called for record: {record.get('title', 'N/A')}")

    # 优先使用生成结果
    results = record.get('files', {}).get('results', [])
    print(f"[DEBUG] Record has {len(results)} result files")

    for i, result in enumerate(results):
        print(f"[DEBUG] Checking result {i}: category={result.get('category')}, has_preview={'preview' in result}")
        if 'preview' in result:
            print(f"[DEBUG] Found preview in result {i}: {result['preview']}")
            return {
                'type': result['category'],
                'data': result['preview']
            }

    # 如果没有生成结果，使用素材
    materials = record.get('files', {}).get('materials', [])
    print(f"[DEBUG] Record has {len(materials)} material files")

    for i, material in enumerate(materials):
        print(f"[DEBUG] Checking material {i}: category={material.get('category')}, has_preview={'preview' in material}")
        if 'preview' in material:
            print(f"[DEBUG] Found preview in material {i}: {material['preview']}")
            return {
                'type': material['category'],
                'data': material['preview']
            }

    print(f"[DEBUG] No preview found, returning None")
    return None

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    """处理文件过大错误"""
    return jsonify({
        'error': f'文件过大！最大允许上传2GB，如果需要更大的文件限制，请修改配置。'
    }), 413

@app.route('/')
def gallery():
    """显示案例画廊首页"""
    return render_template('gallery.html')

@app.route('/record/<record_id>')
def record_detail(record_id):
    """显示案例详情页"""
    return render_template('detail.html', record_id=record_id)

@app.route('/form')
def form():
    """显示表单提交页面"""
    return render_template('form.html')

@app.route('/submit', methods=['POST'])
def submit_record():
    """处理表单提交"""
    try:
        # 获取表单数据
        title = request.form.get('title', '').strip()
        app_id = request.form.get('app_id', '').strip()
        datetime_str = request.form.get('datetime', '').strip()
        params_text = request.form.get('prompt', '').strip()

        # 验证必填字段
        if not all([title, app_id, datetime_str, params_text]):
            return jsonify({'error': '请填写所有必填字段（标题、应用ID、日期时间、参数信息）'}), 400

        # 解析参数信息
        parameters = parse_parameters(params_text)

        # 处理素材文件
        material_files = request.files.getlist('materials')
        materials_list = []

        for file in material_files:
            if file and file.filename:
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)

                    # 获取文件信息
                    category = get_file_category(filename)
                    mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                    file_size = os.path.getsize(filepath)

                    file_info = {
                        'id': str(uuid.uuid4()),
                        'filename': filename,
                        'category': category,
                        'mime_type': mime_type,
                        'size': file_size,
                        'path': f"/uploads/{filename}",
                        'full_path': filepath
                    }

                    # 生成预览信息
                    file_info['preview'] = generate_preview_info(file_info, 'uploads')

                    materials_list.append(file_info)
                else:
                    return jsonify({'error': f'不支持的文件类型: {file.filename}'}), 400

        # 处理生成的结果文件
        result_files = request.files.getlist('results')
        results_list = []

        for file in result_files:
            if file and file.filename:
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(app.config['GENERATED_FOLDER'], filename)
                    file.save(filepath)

                    # 获取文件信息
                    category = get_file_category(filename)
                    mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                    file_size = os.path.getsize(filepath)

                    file_info = {
                        'id': str(uuid.uuid4()),
                        'filename': filename,
                        'category': category,
                        'mime_type': mime_type,
                        'size': file_size,
                        'path': f"/generated/{filename}",
                        'full_path': filepath
                    }

                    # 生成预览信息
                    file_info['preview'] = generate_preview_info(file_info, 'generated')

                    results_list.append(file_info)
                else:
                    return jsonify({'error': f'不支持的文件类型: {file.filename}'}), 400

        # 计算统计数据
        total_size = sum(f['size'] for f in materials_list + results_list)

        # 构建新的数据结构
        record_id = datetime.now().strftime('%Y%m%d%H%M%S%f')
        print(f"[DEBUG] Generated record_id: {record_id}")  # 调试日志
        record = {
            'id': record_id,
            'created_at': datetime.now().isoformat(),
            'title': title,
            'app_id': app_id,
            'generation_time': datetime_str,
            'parameters': parameters,
            'files': {
                'materials': materials_list,
                'results': results_list
            },
            'statistics': {
                'material_count': len(materials_list),
                'result_count': len(results_list),
                'total_size': total_size
            }
        }
        print(f"[DEBUG] Record object ID: {record['id']}")  # 调试日志

        # 保存完整记录到独立文件
        save_record(record)

        # 更新索引（只保存元信息）
        main_preview = get_main_preview(record)
        index_entry = {
            'id': record['id'],
            'created_at': record['created_at'],
            'title': record['title'],
            'app_id': record.get('app_id'),
            'generation_time': record['generation_time'],
            'has_preview': bool(main_preview),
            'preview_type': main_preview['type'] if main_preview else None
        }

        records = load_records()
        records.insert(0, index_entry)  # 最新的记录在前
        save_records(records)

        print(f"[DEBUG] Returning record_id: {record['id']}")  # 调试日志
        print(f"[DEBUG] Full response data keys: {record.keys()}")  # 调试日志
        return jsonify({
            'success': True,
            'message': '记录保存成功',
            'record_id': record['id'],  # 明确返回record_id（放在前面）
            'detail_url': f"/record/{record['id']}",  # 直接返回详情页URL
            'data': record
        })

    except Exception as e:
        return jsonify({'error': f'处理失败: {str(e)}'}), 500

@app.route('/output/<filename>')
def view_output(filename):
    """查看生成的HTML页面"""
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """访问上传的素材文件"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/generated/<filename>')
def generated_file(filename):
    """访问生成的结果文件"""
    return send_from_directory(app.config['GENERATED_FOLDER'], filename)

@app.route('/thumbnails/<filename>')
def thumbnail_file(filename):
    """访问视频缩略图"""
    return send_from_directory(app.config['THUMBNAIL_FOLDER'], filename)

@app.route('/api/records')
def api_records():
    """API: 获取记录列表（分页）"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 12))
        app_id_filter = request.args.get('app_id', '')

        # 加载索引（轻量级）
        index_records = load_records()

        # 按app_id过滤
        if app_id_filter:
            index_records = [r for r in index_records if r.get('app_id') == app_id_filter]

        # 分页
        total = len(index_records)
        start = (page - 1) * per_page
        end = start + per_page
        paginated_index = index_records[start:end]

        # 为每条记录加载完整数据并添加所需字段
        result_records = []
        for index_entry in paginated_index:
            print(f"[DEBUG API] Processing record: {index_entry.get('id')}")
            # 尝试加载完整记录（需要app_id）
            record_app_id = index_entry.get('app_id')
            print(f"[DEBUG API] Record app_id: {record_app_id}")
            if record_app_id:
                full_record = load_record(index_entry['id'], record_app_id)
                print(f"[DEBUG API] Full record loaded: {full_record is not None}")
            else:
                full_record = None
                print(f"[DEBUG API] No app_id, full_record is None")

            if full_record:
                # 使用完整记录的数据
                record = full_record
                record['cover'] = get_cover_image(record)
                record['preview'] = get_main_preview(record)
                print(f"[DEBUG API] Record preview: {record.get('preview')}")
            else:
                # 如果完整记录不存在（旧格式），使用索引数据
                record = index_entry.copy()
                # 为前端提供兼容字段
                record['datetime'] = record.get('generation_time', '')
                if not record.get('title'):
                    record['title'] = '未命名记录'
                print(f"[DEBUG API] Using index entry (no full record)")

            # 确保详情链接存在（使用新的动态详情页）
            if not record.get('detail_url'):
                record['detail_url'] = f"/record/{record['id']}"

            result_records.append(record)

        return jsonify({
            'success': True,
            'data': result_records,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'total_pages': (total + per_page - 1) // per_page
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/apps')
def api_apps():
    """API: 获取所有app_id列表"""
    try:
        records = load_records()
        app_ids = set()
        for record in records:
            if record.get('app_id'):
                app_ids.add(record['app_id'])
        return jsonify({
            'success': True,
            'data': sorted(list(app_ids))
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/record/<record_id>')
def api_record_detail(record_id):
    """API: 获取单个记录的完整详情"""
    try:
        # 从索引中查找记录的app_id
        index_records = load_records()
        app_id = None
        for index_entry in index_records:
            if index_entry['id'] == record_id:
                app_id = index_entry.get('app_id')
                break

        if not app_id:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        # 加载完整记录
        record = load_record(record_id, app_id)
        if not record:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        # 添加额外的展示字段
        record['datetime'] = record.get('generation_time', '')
        record['detail_url'] = f"/record/{record_id}"
        record['cover'] = get_cover_image(record)
        record['preview'] = get_main_preview(record)

        return jsonify({
            'success': True,
            'data': record
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("AI内容生成记录系统启动中...")
    print(f"上传文件夹: {app.config['UPLOAD_FOLDER']}")
    print(f"生成结果文件夹: {app.config['GENERATED_FOLDER']}")
    print(f"输出文件夹: {app.config['OUTPUT_FOLDER']}")
    print(f"数据文件夹: {app.config['DATA_FOLDER']}")
    print(f"记录文件: {RECORDS_DIR}/")
    print(f"索引文件: {INDEX_FILE}")
    print(f"缩略图文件夹: {app.config['THUMBNAIL_FOLDER']}")
    print("\n访问 http://localhost:5000 查看案例画廊")
    print("访问 http://localhost:5000/form 提交新记录")
    app.run(debug=True, host='0.0.0.0', port=5000)
