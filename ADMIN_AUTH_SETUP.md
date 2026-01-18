# 管理员认证设置指南

## 容器环境配置

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

如果python-pam安装失败，可以尝试：

```bash
# Ubuntu/Debian
apt-get update && apt-get install -y libpam0g-dev
pip install python-pam

# CentOS/RHEL
yum install -y pam-devel
pip install python-pam
```

### 2. 容器运行要求

**重要**：容器需要以root权限运行，以便读取/etc/shadow文件进行密码验证。

```bash
# Docker示例
docker run -d --name case-platform \
  --privileged \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/uploads:/app/uploads \
  -v $(pwd)/generated:/app/generated \
  case-platform:latest

# Kubernetes示例（需要privileged或hostPath）
apiVersion: v1
kind: Pod
metadata:
  name: case-platform
spec:
  containers:
  - name: case-platform
    image: case-platform:latest
    ports:
    - containerPort: 5000
    securityContext:
      privileged: true  # 或使用capabilities
```

### 3. 验证方式说明

系统使用以下顺序进行密码验证：

1. **python-pam**（推荐）
   - 使用PAM（可插拔认证模块）
   - 最安全的方式
   - 需要安装python-pam包

2. **spwd + crypt**（备选）
   - 直接读取/etc/shadow验证密码哈希
   - 需要root权限
   - 某些系统可能不支持

3. **简化验证**（fallback）
   - 仅检查用户名为root且密码非空
   - 仅用于开发和测试环境
   - **不推荐用于生产环境**

### 4. 故障排查

#### 问题1：提示"用户名密码错误"

**检查日志**：
```bash
# 查看应用日志
tail -f app.log

# 或查看Docker日志
docker logs -f case-platform
```

**查找认证相关日志**：
- `[Auth] PAM验证成功` - PAM验证成功
- `[Auth] 密码验证成功（使用crypt）` - 哈希验证成功
- `[Auth] 所有验证方法均失败` - 所有方法失败

#### 问题2：python-pam安装失败

**错误信息**：`python-pam模块不可用`

**解决方案**：
```bash
# 安装PAM开发库
# Ubuntu/Debian
apt-get install libpam0g-dev

# CentOS/RHEL
yum install pam-devel

# Alpine
apk add linux-pam-dev

# 然后重新安装python-pam
pip install python-pam
```

#### 问题3：权限不足

**错误信息**：`权限不足读取shadow文件`

**解决方案**：
```bash
# 确保容器以root权限运行
docker exec -it -u 0 case-platform bash

# 或检查文件权限
ls -la /etc/shadow
# 应该显示: -rw-r----- root shadow /etc/shadow
```

### 5. 测试认证功能

#### 方法1：使用curl测试
```bash
# 测试登录
curl -X POST http://localhost:5000/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username":"root","password":"your_password"}'
```

**成功响应**：
```json
{
  "success": true,
  "message": "登录成功",
  "redirect": "/admin/dashboard"
}
```

**失败响应**：
```json
{
  "success": false,
  "error": "用户名或密码错误"
}
```

#### 方法2：使用浏览器测试
1. 访问 `http://localhost:5000/admin/login`
2. 输入用户名：root
3. 输入root用户密码
4. 点击登录

### 6. 安全建议

#### 生产环境配置

1. **修改SECRET_KEY**：
```python
# app.py
app.config['SECRET_KEY'] = 'your-random-secret-key-here'
```

2. **使用HTTPS**：
```python
# 使用Gunicorn + Nginx
gunicorn -w 4 -b 0.0.0.0:5000 app:production
```

3. **限制访问**：
```python
# 只允许特定IP访问管理页面
from flask import request
allowed_ips = ['127.0.0.1', '192.168.1.100']

@app.before_request
def limit_remote_addr():
    if request.path.startswith('/admin') and request.remote_addr not in allowed_ips:
        return abort(403)
```

4. **禁用简化验证**：
```python
# 移除verify_linux_password函数中的方法3（简化验证）
```

### 7. 替代方案

如果容器环境限制较多，可以考虑使用环境变量配置管理员密码：

```python
import os

def verify_admin_password(username, password):
    """使用环境变量验证（替代方案）"""
    # 从环境变量读取管理员密码
    admin_password = os.environ.get('ADMIN_PASSWORD')

    if username == 'root' and password == admin_password:
        return True
    return False
```

**设置环境变量**：
```bash
# Docker
docker run -e ADMIN_PASSWORD=your_password ...

# Kubernetes
env:
  - name: ADMIN_PASSWORD
    valueFrom:
      secretKeyRef:
        name: admin-secret
        key: password
```

## 快速测试

1. **重启应用**：
```bash
python app.py
```

2. **查看日志**：
```
[Auth] PAM验证成功
或
[Auth] 密码验证成功（使用crypt）
```

3. **测试登录**：
- 访问 http://localhost:5000/admin/login
- 使用root账号和系统密码登录

## 支持

如果遇到问题，请提供以下信息：
1. 操作系统版本：`cat /etc/os-release`
2. Python版本：`python --version`
3. 应用日志输出
4. 错误截图或完整错误信息
