# Hướng dẫn sử dụng Ludivoxa

## Cài bản Windows offline

Từ [Releases](https://github.com/hungdinhxuan/Ludivoxa/releases), tải cùng phiên bản:

- `Ludivoxa-1.0.0-Full-Offline-Setup.exe`
- `Ludivoxa-1.0.0-Full-Offline-Setup-1.bin`
- `Ludivoxa-1.0.0-Full-Offline-Setup-2.bin`
- `Ludivoxa-1.0.0-Full-Offline-Setup-3.bin`
- `SHA256SUMS.txt`, `README.txt` và `release-manifest.json` nếu cần đối chiếu.

Đặt EXE và cả ba BIN trong cùng thư mục, rồi chạy EXE. Không chạy từng BIN.
Không dùng nút **Source code** để cài app: đó là mã nguồn, không chứa runtime
và model. Bản offline có dung lượng khoảng 4,9 GB; app cài xong khoảng 6,4 GB.
Python và Ollama đã có sẵn trong bộ cài. Cài đặt mặc định theo tài khoản người
dùng; nâng cấp giữ `config.yml` hiện có.

## Dịch hội thoại

1. Mở game, mở Ludivoxa, chọn **Refresh** rồi chọn đúng cửa sổ game.
2. Chờ OCR, translation và voice sẵn sàng, rồi chọn **Start Capture**.
3. Mở **Configure OCR** → **Scan: Only selected box**.
4. Kéo khoanh phần hội thoại và nhấn **OK**.

Box xanh có thể kéo để di chuyển, kéo góc để đổi kích thước, hoặc chỉnh bằng
các ô phần trăm. **Use bottom dialogue box** tạo vùng khởi đầu ở phía dưới.
**Clear Box** cho phép vẽ lại. Box được lưu riêng cho từng tên cửa sổ/nguồn,
theo tỷ lệ ảnh nên vẫn dùng được khi đổi độ phân giải.

Chỉ box đó được đưa vào OCR. Trong lúc chỉnh, app chỉ xem trước kết quả OCR,
không dịch và không đọc TTS. **Cancel** không lưu chỉnh sửa. Box trống không
tự quay về quét cả màn hình. **Full window (use exclusions)** dùng lại cách
exclude cũ; các exclude cũ vẫn được giữ khi chuyển chế độ.

Vùng chọn không tự đi theo chữ. Nếu game chuyển hội thoại sang vị trí khác,
hãy chỉnh lại box. Sau khi đóng phần cấu hình, câu đang hiện có thể được đọc
một lần trong phiên xử lý mới.

## Ngôn ngữ và engine

- Game tiếng Anh: chọn **English**, PaddleOCR và model nhận dạng
  `models/en_PP-OCRv5_mobile_rec` để ưu tiên tốc độ.
- Game tiếng Nhật: chọn **Japanese** và `models/PP-OCRv5_mobile_rec`.
- Game có cả Anh/Nhật: dùng **Auto** cùng `models/PP-OCRv5_mobile_rec`.
  Nếu phát hiện sai ngôn ngữ, chọn ngôn ngữ thủ công.
- Target mặc định: **Vietnamese**. Có thể chọn ngôn ngữ dịch khác; hãy dùng
  **Overlay only** khi target không phải tiếng Việt vì các giọng đi kèm chỉ đọc
  tiếng Việt.

MeikiOCR, Sugoi V4 và owocr vẫn có trong ứng dụng. Sugoi dành cho Nhật → Anh.
owocr là chương trình riêng; việc dùng engine online trong owocr là lựa chọn
thủ công, không phải fallback của profile offline.

## Overlay và giọng đọc

Chọn **Overlay only**, **TTS only** hoặc **Overlay + TTS** trong Session.
**F8** ẩn/hiện overlay; ẩn overlay không dừng giọng đọc.
**Stop reading** dừng âm thanh hiện tại. Dừng capture hoặc chọn Overlay only
cũng dừng TTS.

Chọn **Voice model**, **Speaker**, chỉnh **Speaking speed**, rồi dùng **Test
voice** để nghe thử. Bản đầy đủ có 11 preset VieNeu Nano và 67 lựa chọn Piper.
Với Nano, **Clone from audio** nhận một file ghi âm sạch trên máy; không cần
transcript. Để trống thì dùng preset. File tham chiếu không được upload.

Âm thanh của cả câu được tạo trước khi phát để tránh hụt giữa câu. Vì vậy đoạn
dài vẫn mất thời gian chuẩn bị; câu mới cũng có thể chờ câu đang đọc kết thúc.
App chỉ giữ bản dịch chờ mới nhất và hủy phần tổng hợp đã lỗi thời khi có thể.

## Ollama local và GPU server LAN

Bộ cài mặc định dùng Ollama riêng ở `http://127.0.0.1:11435`, model
`qwen3-hauhau-aggressive:4b-q4_k_m`. App tự mở runtime và không tự chuyển sang
cloud. Đóng cửa sổ app cuối cùng sẽ dừng runtime/model runners và giải phóng tài
nguyên. Mở lại phải tải model lại. Một cửa sổ khác từ cùng bản cài còn mở sẽ
giữ service hoạt động. Runtime không thuộc quyền quản lý của app được giữ nguyên.

Để dùng server riêng, trong Engines chọn:

| Backend | Ví dụ URL | Model |
| --- | --- | --- |
| Ollama local bên ngoài app | `http://127.0.0.1:11434` | Tên model trên server |
| Ollama LAN | `http://192.168.1.10:11434` | Tên model trên server |
| OpenAI-compatible LAN | `http://192.168.1.10:8000/v1` | Model ID trên server |

Chạy server đó trước, cấu hình để máy này truy cập được qua mạng riêng, rồi
**Test** và **Apply**. App không tự tắt server LAN hoặc server local dùng chung.
Prompt và số dòng context có thể chỉnh/cấu hình theo engine hiện có.

## Xử lý sự cố

- OCR nhận lẫn menu/nền: thu nhỏ box, chọn đúng model/ngôn ngữ, chỉnh confidence.
- Chữ bị cắt: nới box, đặc biệt khi game cuộn hoặc xuống dòng.
- Đọc lặp: kiểm tra có mở nhiều phiên capture hay không và box có chứa phần
  nhấp nháy không. Chống lặp giảm nhiễu OCR nhưng không sửa mọi lỗi nhận dạng.
- Chậm: chọn English cho game tiếng Anh, giữ box gọn; lần đầu cần tải model.
  Thời gian OCR không phải toàn bộ độ trễ đến tiếng đầu tiên.
- Endpoint lỗi: kiểm tra URL/model, chờ Ready, rồi dùng Fix Models nếu cần.
- Settings nằm trong `config.yml` cạnh EXE; log ở `%USERPROFILE%/.interpreter/logs`.
  Các tên tương thích này được giữ từ Interpreter.

Xem [changelog](../CHANGELOG.md), [phát triển/build](DEVELOPMENT.md),
[số đo và giới hạn](PERFORMANCE.md), [ghi công/giấy phép](../NOTICE.md).
