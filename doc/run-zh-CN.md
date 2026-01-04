# MikuInvidious 最简配置教程（不安全）

本教程旨在在本地环境配置 MikuInvidious ASGI Server 进行测试，生产环境像是使用反向代理等，可参考compose.yml。

## 1. 准备环境

### 推荐环境

- **Python 3.11+**
- **Redis**: 用于 Session 和数据快取。

### 安装环境与 Redis

请参考 [redis 安装文档](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-linux/)

```bash
sudo apt install python3 python3-venv git
```

---

## 2. 快速启动

### 下载代码与建立环境

```bash
git clone https://github.com/apicalshark/mikuinvidious.git
cd mikuinvidious

# 建立并进入虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖项
pip install -r requirements.txt
```

---

## 3. 最小化配置（完整内容可参考 python/shared.py）

复制示例配置：

```bash
cp config.toml.sample config.toml
```

**开发时的关键配置：**

- **`[proxy]`**:
  - 可新增 `proxy_url` 设置你的 SOCKS5/HTTP 代理（例如 cf warp `http://127.0.0.1:1080`）。

---

## 4. 运行

在项目根目录下执行：

```bash
# 设置 Python 路径并启动
python3 python/main.py
```

启动后，直接访问 `http://localhost:8888` 即可。

---

## 提示

- **认证**: 应避免，但如果需要调试 1080P 或会员功能，请在 `config.toml` 的 `[credential]` 填入你的 B 站 Cookie 并将 `use_cred` 设为 `true`。
