from flask import Flask, request, render_template, jsonify, send_from_directory, session, redirect, url_for
from werkzeug.exceptions import RequestEntityTooLarge
import os
import json
from datetime import datetime
from werkzeug.utils import secure_filename
import mimetypes
import uuid
import subprocess
import functools
import hashlib
import base64

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
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'  # 用于session加密

# .auth文件路径
AUTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.auth')

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

# 审核状态常量
STATUS_PENDING = 'pending'  # 待审核
STATUS_APPROVED = 'approved'  # 已审核通过
STATUS_REJECTED = 'rejected'  # 已拒绝

# ==================== 管理员认证函数 ====================

def hash_password(password):
    """使用SHA256哈希密码"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def load_auth_data():
    """加载.auth文件"""
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[Auth] 读取.auth文件失败: {e}")
            return None
    return None

def save_auth_data(auth_data):
    """保存到.auth文件"""
    try:
        with open(AUTH_FILE, 'w', encoding='utf-8') as f:
            json.dump(auth_data, f, ensure_ascii=False, indent=2)
        # 设置文件权限为只有所有者可读写
        os.chmod(AUTH_FILE, 0o600)
        return True
    except Exception as e:
        print(f"[Auth] 保存.auth文件失败: {e}")
        return False

def check_admin_exists():
    """检查是否已创建管理员账号"""
    auth_data = load_auth_data()
    return auth_data is not None

def verify_admin_credentials(username, password):
    """验证管理员账号密码"""
    auth_data = load_auth_data()
    if not auth_data:
        return False

    if auth_data.get('username') == username:
        hashed_password = hash_password(password)
        if hashed_password == auth_data.get('password_hash'):
            return True

    return False

def create_admin_account(username, password):
    """创建管理员账号"""
    auth_data = {
        'username': username,
        'password_hash': hash_password(password),
        'created_at': datetime.now().isoformat()
    }

    if save_auth_data(auth_data):
        print(f"[Auth] 管理员账号创建成功: {username}")
        return True
    return False

def update_admin_password(new_password):
    """更新管理员密码"""
    auth_data = load_auth_data()
    if not auth_data:
        return False

    auth_data['password_hash'] = hash_password(new_password)
    auth_data['updated_at'] = datetime.now().isoformat()

    if save_auth_data(auth_data):
        print(f"[Auth] 密码更新成功: {auth_data['username']}")
        return True
    return False

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
            },
            'status': STATUS_PENDING,  # 新案例默认为待审核状态
            'review_status': 'pending'  # 兼容字段
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
            'preview_type': main_preview['type'] if main_preview else None,
            'status': STATUS_PENDING  # 索引中也保存状态
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

        # 只显示已审核通过的案例（公开API）
        index_records = [r for r in index_records if r.get('status') == STATUS_APPROVED]

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
    """API: 获取所有app_id列表（仅已审核通过的案例）"""
    try:
        records = load_records()
        app_ids = set()
        for record in records:
            # 只统计已审核通过的案例
            if record.get('app_id') and record.get('status') == STATUS_APPROVED:
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
        # 从索引中查找记录的app_id和状态
        index_records = load_records()
        app_id = None
        record_status = None
        for index_entry in index_records:
            if index_entry['id'] == record_id:
                app_id = index_entry.get('app_id')
                record_status = index_entry.get('status', STATUS_PENDING)
                break

        if not app_id:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        # 检查审核状态和用户权限
        # 管理员可以查看所有状态的案例，普通用户只能查看已审核通过的
        is_admin = session.get('logged_in', False)
        if not is_admin and record_status != STATUS_APPROVED:
            return jsonify({
                'success': False,
                'error': '该案例正在审核中，暂不可查看'
            }), 403

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

# ==================== 管理员认证和审核管理功能 ====================

def verify_linux_password(username, password):
    """验证管理员账号密码（使用.auth文件）"""
    return verify_admin_credentials(username, password)

def login_required(f):
    """管理员登录验证装饰器"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session['logged_in']:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """管理员登录页面"""
    if request.method == 'GET':
        # 检查是否已创建管理员账号
        if not check_admin_exists():
            # 未创建，显示创建账号页面
            return render_template('admin_setup.html')
        # 已创建，显示登录页面
        return render_template('admin_login.html')

    # POST请求 - 处理登录或创建账号
    data = request.get_json()
    action = data.get('action', 'login')  # login 或 create

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400

    if action == 'create':
        # 创建管理员账号
        if check_admin_exists():
            return jsonify({'success': False, 'error': '管理员账号已存在'}), 400

        if len(username) < 3:
            return jsonify({'success': False, 'error': '用户名至少3个字符'}), 400

        if len(password) < 6:
            return jsonify({'success': False, 'error': '密码至少6个字符'}), 400

        if password != data.get('confirm_password', ''):
            return jsonify({'success': False, 'error': '两次输入的密码不一致'}), 400

        if create_admin_account(username, password):
            session['logged_in'] = True
            session['username'] = username
            session.permanent = True
            return jsonify({
                'success': True,
                'message': '管理员账号创建成功',
                'redirect': '/admin/dashboard'
            })
        else:
            return jsonify({'success': False, 'error': '创建账号失败'}), 500

    elif action == 'login':
        # 登录验证
        if not check_admin_exists():
            return jsonify({'success': False, 'error': '请先创建管理员账号'}), 400

        if verify_admin_credentials(username, password):
            session['logged_in'] = True
            session['username'] = username
            session.permanent = True
            return jsonify({
                'success': True,
                'message': '登录成功',
                'redirect': '/admin/dashboard'
            })
        else:
            return jsonify({'success': False, 'error': '用户名或密码错误'}), 401

    else:
        return jsonify({'success': False, 'error': '无效的操作'}), 400

@app.route('/admin/logout')
def admin_logout():
    """管理员登出"""
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    """管理员控制台"""
    return render_template('admin_dashboard.html')

@app.route('/admin/api/records')
@login_required
def admin_api_records():
    """API: 获取所有案例列表（包括待审核的）"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        status_filter = request.args.get('status', '')
        app_id_filter = request.args.get('app_id', '')

        # 加载索引
        index_records = load_records()

        # 按状态过滤
        if status_filter:
            index_records = [r for r in index_records if r.get('status') == status_filter]

        # 按app_id过滤
        if app_id_filter:
            index_records = [r for r in index_records if r.get('app_id') == app_id_filter]

        # 分页
        total = len(index_records)
        start = (page - 1) * per_page
        end = start + per_page
        paginated_index = index_records[start:end]

        # 加载完整数据
        result_records = []
        for index_entry in paginated_index:
            record_app_id = index_entry.get('app_id')
            if record_app_id:
                full_record = load_record(index_entry['id'], record_app_id)
                if full_record:
                    result_records.append(full_record)
            else:
                # 旧格式兼容
                record = index_entry.copy()
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

@app.route('/admin/api/record/<record_id>', methods=['GET', 'DELETE'])
@login_required
def admin_api_record_detail(record_id):
    """API: 获取或删除单个案例"""
    try:
        # 从索引中查找记录的app_id
        index_records = load_records()
        app_id = None
        index_entry = None

        for entry in index_records:
            if entry['id'] == record_id:
                app_id = entry.get('app_id')
                index_entry = entry
                break

        if not app_id:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        if request.method == 'DELETE':
            # 删除记录
            # 1. 删除完整记录文件
            app_dir = os.path.join(RECORDS_DIR, app_id)
            record_file = os.path.join(app_dir, f"{record_id}.json")
            if os.path.exists(record_file):
                os.remove(record_file)

            # 2. 从索引中移除
            index_records.remove(index_entry)
            save_records(index_records)

            return jsonify({
                'success': True,
                'message': '删除成功'
            })

        # GET请求 - 返回完整记录
        record = load_record(record_id, app_id)
        if not record:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        return jsonify({
            'success': True,
            'data': record
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/api/review/<record_id>', methods=['POST'])
@login_required
def admin_api_review(record_id):
    """API: 审核案例（通过/拒绝）"""
    try:
        data = request.get_json()
        action = data.get('action')  # 'approve' 或 'reject'
        reason = data.get('reason', '')  # 拒绝原因（可选）

        if action not in ['approve', 'reject']:
            return jsonify({'success': False, 'error': '无效的操作'}), 400

        # 从索引中查找记录
        index_records = load_records()
        app_id = None
        index_entry = None

        for entry in index_records:
            if entry['id'] == record_id:
                app_id = entry.get('app_id')
                index_entry = entry
                break

        if not app_id:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        # 加载完整记录
        record = load_record(record_id, app_id)
        if not record:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        # 更新状态
        new_status = STATUS_APPROVED if action == 'approve' else STATUS_REJECTED
        record['status'] = new_status
        record['review_status'] = new_status  # 兼容字段

        if action == 'reject' and reason:
            record['reject_reason'] = reason

        # 保存完整记录
        save_record(record)

        # 更新索引
        index_entry['status'] = new_status
        save_records(index_records)

        return jsonify({
            'success': True,
            'message': f'案例已{"通过" if action == "approve" else "拒绝"}审核',
            'data': {
                'record_id': record_id,
                'status': new_status
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/api/stats')
@login_required
def admin_api_stats():
    """API: 获取统计信息"""
    try:
        index_records = load_records()

        stats = {
            'total': len(index_records),
            'pending': 0,
            'approved': 0,
            'rejected': 0,
            'by_app': {}
        }

        for record in index_records:
            status = record.get('status', STATUS_PENDING)
            if status == STATUS_PENDING:
                stats['pending'] += 1
            elif status == STATUS_APPROVED:
                stats['approved'] += 1
            elif status == STATUS_REJECTED:
                stats['rejected'] += 1

            # 按应用统计
            app_id = record.get('app_id', 'unknown')
            if app_id not in stats['by_app']:
                stats['by_app'][app_id] = {'total': 0, 'pending': 0, 'approved': 0}
            stats['by_app'][app_id]['total'] += 1
            if status == STATUS_PENDING:
                stats['by_app'][app_id]['pending'] += 1
            elif status == STATUS_APPROVED:
                stats['by_app'][app_id]['approved'] += 1

        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/api/change-password', methods=['POST'])
@login_required
def admin_change_password():
    """API: 修改管理员密码"""
    try:
        data = request.get_json()
        old_password = data.get('old_password', '')
        new_password = data.get('new_password', '')
        confirm_password = data.get('confirm_password', '')

        # 验证旧密码
        username = session.get('username')
        if not verify_admin_credentials(username, old_password):
            return jsonify({'success': False, 'error': '原密码错误'}), 400

        # 验证新密码
        if len(new_password) < 6:
            return jsonify({'success': False, 'error': '新密码至少6个字符'}), 400

        if new_password != confirm_password:
            return jsonify({'success': False, 'error': '两次输入的新密码不一致'}), 400

        # 更新密码
        if update_admin_password(new_password):
            return jsonify({
                'success': True,
                'message': '密码修改成功'
            })
        else:
            return jsonify({'success': False, 'error': '密码修改失败'}), 500

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/api/batch', methods=['POST'])
@login_required
def admin_batch_operation():
    """API: 批量操作（审核、删除）"""
    try:
        data = request.get_json()
        action = data.get('action')  # 'approve', 'reject', 'delete'
        record_ids = data.get('record_ids', [])
        reason = data.get('reason', '')  # 拒绝原因（可选）

        if not action:
            return jsonify({'success': False, 'error': '缺少操作类型'}), 400

        if not record_ids or not isinstance(record_ids, list):
            return jsonify({'success': False, 'error': '缺少记录ID列表'}), 400

        if len(record_ids) == 0:
            return jsonify({'success': False, 'error': '记录ID列表为空'}), 400

        # 加载索引
        index_records = load_records()

        results = {
            'success': True,
            'total': len(record_ids),
            'succeeded': 0,
            'failed': 0,
            'errors': []
        }

        # 执行批量操作
        for record_id in record_ids:
            try:
                # 查找索引中的记录
                index_entry = None
                for entry in index_records:
                    if entry['id'] == record_id:
                        index_entry = entry
                        break

                if not index_entry:
                    results['errors'].append(f"{record_id}: 记录不存在")
                    results['failed'] += 1
                    continue

                app_id = index_entry.get('app_id')
                if not app_id:
                    results['errors'].append(f"{record_id}: 缺少app_id")
                    results['failed'] += 1
                    continue

                if action == 'delete':
                    # 删除操作
                    app_dir = os.path.join(RECORDS_DIR, app_id)
                    record_file = os.path.join(app_dir, f"{record_id}.json")
                    if os.path.exists(record_file):
                        os.remove(record_file)

                    index_records.remove(index_entry)

                elif action in ['approve', 'reject']:
                    # 审核操作
                    record = load_record(record_id, app_id)
                    if not record:
                        results['errors'].append(f"{record_id}: 无法加载记录")
                        results['failed'] += 1
                        continue

                    new_status = STATUS_APPROVED if action == 'approve' else STATUS_REJECTED
                    record['status'] = new_status
                    record['review_status'] = new_status

                    if action == 'reject' and reason:
                        record['reject_reason'] = reason

                    save_record(record)
                    index_entry['status'] = new_status

                results['succeeded'] += 1

            except Exception as e:
                results['errors'].append(f"{record_id}: {str(e)}")
                results['failed'] += 1

        # 保存索引（如果有删除或审核操作）
        if action in ['delete', 'approve', 'reject']:
            save_records(index_records)

        return jsonify({
            'success': True,
            'message': f'批量操作完成：成功 {results["succeeded"]} 个，失败 {results["failed"]} 个',
            'data': results
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
