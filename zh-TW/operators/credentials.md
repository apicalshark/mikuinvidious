# Bilibili 憑證

未設定憑證時系統仍可正常運作，匿名存取的最高畫質為 1080P。若需更高畫質、付費或會員限定內容，才需要設定憑證。

## 取得方式

1. 於瀏覽器登入 `bilibili.com`。
2. 開啟開發者工具 → Application → Cookies，記錄 `SESSDATA`、`bili_jct`、`buvid3`、`buvid4`、`DedeUserID`。
3. `ac_time_value` 位於同一頁面的 Local Storage（`window.localStorage.ac_time_value`）。

詳細圖文步驟可參閱 [bilibili-api 憑證取得教學](https://nemo2011.github.io/bilibili-api/#/get-credential)。

## 設定方式

於 `config.toml` 的 `[credential]` 區段填入上述數值，並將 `use_cred` 設為 `true`；Docker 部署則使用同名環境變數（`SESSDATA`、`BILI_JCT`、`BUVID3`、`BUVID4`、`DEDEUSERID`、`USE_CRED=true`）。

## 加密存放（建議）

以明文存放 Cookie 於組態檔存在外洩風險。本專案支援 libsodium（XChaCha20-Poly1305）加密：

```bash
python tools/encrypt_secrets.py --generate-key   # 產生主金鑰，存放於 SECRETS_MASTER_KEY
python tools/encrypt_secrets.py --encrypt SESSDATA "實際數值"
```

將輸出的 base64 字串貼回組態檔，系統啟動時將自動解密。

## 注意事項

- 憑證等同於帳號存取權限，**不可**提交至公開儲存庫或提供給他人。
- 憑證逾期的徵兆：高畫質無法取得、付費劇集僅顯示預告。重新取得並置換即可。
- 公開實例啟用 `SITE_SHOW_UNSAFE_ERROR_RESPONSE` 將向訪客顯示錯誤細節，除錯完成後請關閉。
