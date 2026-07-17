# H200 本機 AI 伺服器建置紀錄

> 給誰看：在 H200 GPU 主機上執行的 Claude Code（或任何會讀這份文件並動手裝機的 agent）。
> 目的：把一台**什麼都沒裝**的 H200 伺服器，架成可以離線跑開源 LLM 推論的伺服器，
> 之後供「大戶加碼篩選」這個專案（`chendiuan/High-roller`）使用，取代 `generate_agent_analysis.py`
> 目前的規則式模板，改用真的語言模型生成分析文字。
>
> **這份文件的範圍只到「AI 推論伺服器裝好、可以呼叫」為止。** 把它接回
> `generate_agent_analysis.py`（修改股票分析三個分頁的產生邏輯）是後續獨立的任務，
> 這裡先不動這個 repo 既有的程式碼。

## 什麼時候用這份文件

- 這台 H200 伺服器是全新的（沒有 driver、沒有 CUDA、沒有任何推論框架）
- 有人（通常是 Dean）要求把它架成本機 LLM 推論伺服器
- 執行者是自動化 agent（例如 Claude Code），需要能照著步驟自己判斷、自己裝

## 前置確認（開始裝之前先跑）

在動手安裝前，先蒐集以下資訊，因為後面「選模型」那步要用：

```bash
# 作業系統
cat /etc/os-release

# GPU 數量與每張卡的顯存
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv

# CUDA 版本（如果 driver 已裝）
nvidia-smi | grep "CUDA Version"

# 可用磁碟空間（模型權重動輒數十到數百 GB）
df -h /

# 是否有 sudo / root
whoami; sudo -n true 2>/dev/null && echo "有 sudo" || echo "需要密碼或沒有 sudo"
```

把這幾項的結果記住，會決定下面「選幾 B 的模型」跟「要不要 tensor-parallel」。

⚠️ 如果 `nvidia-smi` 直接失敗（找不到指令），代表連 NVIDIA driver 都沒裝，先處理
「步驟 1：驅動與 CUDA」；如果 `nvidia-smi` 正常但看不到 H200，先停下來跟 Dean 確認
是不是接錯卡或 driver 沒認到，不要自己亂猜著裝。

## 步驟 1：NVIDIA Driver / CUDA

多數 GPU 雲主機（Lambda、CoreWeave、RunPod 這類）出廠就會附 driver。先確認：

```bash
nvidia-smi
```

如果能看到 GPU 資訊且 CUDA Version ≥ 12.4，直接跳到步驟 2。

如果沒有，才需要自己裝（Ubuntu 範例，H200 需要 driver 550 以上才有完整支援）：

```bash
sudo apt update
sudo apt install -y ubuntu-drivers-common
sudo ubuntu-drivers install
sudo reboot
# 重開機後再跑一次 nvidia-smi 確認
```

## 步驟 2：Python 環境

用獨立虛擬環境，不要動系統 Python：

```bash
python3 --version   # 需要 3.10–3.12
sudo apt install -y python3-venv python3-pip

python3 -m venv ~/ai-server-env
source ~/ai-server-env/bin/activate
pip install --upgrade pip
```

之後每次要啟動推論伺服器前，都要先 `source ~/ai-server-env/bin/activate`。

## 步驟 3：安裝 vLLM

這個專案的使用情境（幾千檔股票、每檔要塞大戶籌碼+股價+YouTube 逐字稿當 context、
一次批次跑）比較適合 **vLLM**：吞吐量高、PagedAttention 對長 context 記憶體管理好、
原生支援 H200 的 FP8 Tensor Core、有 OpenAI 相容 API，方便之後串接。

```bash
pip install vllm
```

> 如果 `pip install vllm` 裝起來跟目前的 CUDA 版本兜不起來（版本衝突、抓不到對應 wheel），
> 到 https://docs.vllm.ai/ 查當時對應 CUDA 版本的安裝指令，這份文件寫的時間點可能已經
> 有更新的版本。裝之前用 `pip index versions vllm` 或直接查官網，不要死守這裡的版本號。

備用方案：如果 vLLM 裝不起來，或只是想先簡單驗證 GPU 能跑模型，可以先用
**Ollama**（`curl -fsSL https://ollama.com/install.sh | sh`）快速測試，操作簡單很多，
但吞吐量與長 context 效能不如 vLLM，正式跑這個專案的批次分析建議還是用 vLLM。

## 步驟 4：選模型

**先上網搜尋當下（現在的日期）最新的開源模型排行與推薦**，不要只憑這份文件裡寫的
型號 —— 開源 LLM 更新很快，這份文件寫成之後很可能就有更新的選擇。搜尋方向：
「open weight LLM leaderboard [當前年月]」「best open source LLM for finance [當前年月]」。

選模型的判斷邏輯：

| 偵測到的硬體 | 建議 |
|---|---|
| 單張 H200（141GB）| 選 30B 上下的 dense 模型（例如 Qwen 系列 32B 級），用 BF16 或 FP8 皆可，換來大量 KV cache 空間餵長逐字稿 |
| 單張 H200，想衝更大模型 | 選有 INT4/AWQ 量化版本的 100B 級模型，但要抓緊記憶體，KV cache 空間會很緊，長 context 容易 OOM |
| 多張 H200（4~8 張）| 可以上大型 MoE 模型（100B+ 總參數），用 `--tensor-parallel-size` 依 GPU 數量切分 |

篩選模型時的硬性條件：

- **中文（尤其是繁體中文）能力要好** —— 這個專案的資料幾乎全是繁體中文（台股新聞、
  YouTube 逐字稿、大戶籌碼術語），選型時看基準測試裡有沒有中文/繁中的分項成績
- **授權要確認清楚** —— 有些模型（例如 Llama 系列）需要在 Hugging Face 上申請 gated
  access、同意授權條款才能下載；下載前確認授權允許這種用途（個人非商業分析工具通常沒問題，
  但還是要看清楚條款，不要跳過）
- **支援長 context**（建議至少 32K，能到 128K 更好）—— 因為 YouTube 逐字稿一支影片
  可能就有幾千字，加上多支影片、大戶籌碼資料一起餵，很容易超過短 context 模型的上限

選定後記錄下「選了哪個模型、為什麼、用了什麼量化」，寫回這台伺服器上的
`~/ai-server-notes.md`（不用放進這個 git repo，純本機記錄），方便之後追蹤。

## 步驟 5：下載模型權重

```bash
pip install "huggingface_hub[cli]"
huggingface-cli login   # 如果是 gated 模型才需要，會要求貼 HF token

# 範例（實際型號依步驟 4 選定的結果替換）
huggingface-cli download <org>/<model-name> --local-dir ~/models/<model-name>
```

下載前務必用步驟 0 記下的可用磁碟空間對一下，模型權重常常是幾十到幾百 GB，
下載到一半沒空間比裝到一半失敗更麻煩。

## 步驟 6：啟動推論伺服器

```bash
source ~/ai-server-env/bin/activate

vllm serve ~/models/<model-name> \
  --host 127.0.0.1 \
  --port 8000 \
  --api-key "$(openssl rand -hex 32)" \
  --max-model-len 32768
```

⚠️ 安全性重點：

- `--host 127.0.0.1` 只綁本機，不要一開始就綁 `0.0.0.0` 對外開放，除非明確要給外部
  存取（那要另外討論防火牆、反向代理、HTTPS，屬於需要 Dean 拍板的決定，不要自己開）
- 一定要帶 `--api-key`，不要跑一個沒有驗證的公開推論端點
- 把實際用的 API key 存到伺服器上的 `~/.ai-server.env`（**不要**放進這個 git repo，
  加進 `.gitignore` 或乾脆不放在 repo 目錄底下）

如果多張 H200，依 GPU 數量加上 tensor-parallel：

```bash
vllm serve ~/models/<model-name> \
  --tensor-parallel-size <GPU數量> \
  --host 127.0.0.1 --port 8000 --api-key "$(cat ~/.ai-server.env)"
```

## 步驟 7：驗證

另開一個終端機（或背景執行 vllm serve 之後）測試：

```bash
curl http://127.0.0.1:8000/v1/models

curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer <你的 api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<model-name>",
    "messages": [{"role":"user","content":"用一句話說明什麼是大戶籌碼分析"}]
  }'
```

看到正常的中文回應就算成功。也用 `nvidia-smi` 確認有吃到 GPU（顯存被佔用、有 process）。

## 步驟 8：常駐化（選用但建議）

用 systemd 讓伺服器重開機後自動啟動，不用每次手動跑：

```bash
sudo tee /etc/systemd/system/vllm-server.service <<'EOF'
[Unit]
Description=vLLM Inference Server
After=network.target

[Service]
Type=simple
User=<你的使用者名稱>
EnvironmentFile=/home/<你的使用者名稱>/.ai-server.env
ExecStart=/home/<你的使用者名稱>/ai-server-env/bin/vllm serve /home/<你的使用者名稱>/models/<model-name> --host 127.0.0.1 --port 8000 --api-key ${VLLM_API_KEY}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now vllm-server
sudo systemctl status vllm-server
```

## 疑難排解

| 狀況 | 排查方向 |
|---|---|
| `nvidia-smi` 找不到指令 | driver 沒裝，回步驟 1 |
| CUDA out of memory | 模型太大或 `--max-model-len` 設太高，換小一點的模型/量化版本，或調低 context 上限 |
| vLLM 裝不起來（版本衝突）| 查 vLLM 官網當下對應目前 CUDA 版本的安裝方式，或改用 Ollama 先跑起來 |
| 下載模型中斷 | `huggingface-cli download` 支援續傳，重新執行同一行指令即可 |
| API 呼叫沒回應/很慢 | 確認 `nvidia-smi` 有負載；第一次呼叫會有模型載入/暖機時間，屬正常 |

## 完成後回報

裝完、驗證過（步驟 7 成功）之後，回報：

1. 選了哪個模型、多少參數、什麼量化
2. GPU 顯存使用狀況（`nvidia-smi` 截圖或文字輸出）
3. API endpoint 位置（`http://127.0.0.1:8000`，是否常駐化）
4. 有沒有遇到步驟以外的問題、怎麼解的

**不要**自己動手改這個 repo 裡的 `generate_agent_analysis.py` 或 `dashboard.html` 去接這個
新伺服器 —— 那是下一步，等這份環境確認穩定之後再另外討論怎麼整合。
