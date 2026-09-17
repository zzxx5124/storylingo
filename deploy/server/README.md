# 語閱 StoryLingo Server 部署包

此資料夾是小說網站正式 Server 的部署設定。網站本身不包含 TTS runtime、模型、CUDA 或 GPU 設定。

## 部署前

1. 將 release package 上傳到 Linux Server。
2. 將 `.env.example` 複製為 `.env`，填入正式密碼與 AI key。
3. 將 `reader.example.com` 的 DNS A/AAAA 記錄指向此 Server。
4. 使用主機現有的 Nginx/Apache/aaPanel 反向代理到 `127.0.0.1:8000`。

## 啟動

```bash
cp .env.example .env
mkdir -p runtime/{data,storage,uploads,logs}
docker compose up -d --build
docker compose ps
curl -I https://reader.example.com/api/health/live
```

主機 Nginx 最小設定：

```nginx
server {
    listen 443 ssl http2;
    server_name reader.example.com;
    # ssl_certificate /path/to/fullchain.pem;
    # ssl_certificate_key /path/to/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 1800s;
    }
}
```

如果這台主機沒有既有的 80/443 服務，才使用 `Caddyfile` 與獨立 Caddy compose 設定。

第一次啟動後，請登入管理員後台設定遠端 TTS provider。TTS API 建議使用 `tts.example.com` 或 Cloudflare Tunnel 對外提供 HTTPS。
