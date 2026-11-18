# Hướng dẫn sử dụng Ludivoxa

## Cài bản Windows offline

Người dùng mới: từ [bộ offline v1.1.1](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.1.1), tải cùng phiên bản:

- `Ludivoxa-1.1.1-Full-Offline-Setup.exe`
- `Ludivoxa-1.1.1-Full-Offline-Setup-1.bin`
- `Ludivoxa-1.1.1-Full-Offline-Setup-2.bin`
- `Ludivoxa-1.1.1-Full-Offline-Setup-3.bin`
- `SHA256SUMS.txt`, `README.txt` và `release-manifest.json` nếu cần đối chiếu.

Đặt EXE và cả ba BIN trong cùng thư mục, rồi chạy EXE. Không chạy từng BIN.
Không dùng nút **Source code** để cài app: đó là mã nguồn, không chứa runtime
và model. Bản offline có dung lượng khoảng 4,9 GB; app cài xong khoảng 6,4 GB.
Python và Ollama đã có sẵn trong bộ cài. Cài đặt mặc định theo tài khoản người
dùng. Sau đó cài **v1.2.0 App-Update** qua tab **Updates** hoặc
[bản phát hành ổn định](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.2.0).

## Dịch hội thoại

1. Mở game, mở Ludivoxa, chọn **Refresh** rồi chọn đúng cửa sổ game.
2. Chờ OCR, translation và voice sẵn sàng, rồi chọn **Start Capture**.
3. Mở **Configure OCR** → **Scan: Only selected box**.
4. Kéo khoanh phần hội thoại và nhấn **OK**.

Từ v1.1.3, **Configure OCR** nằm trên thanh đầu cửa sổ app. Trong dialog,
**OK / Cancel / Clear** luôn ở trên cùng; nội dung bên dưới có thể cuộn và
preview tự co theo kích thước cửa sổ để dùng trên màn hình remote nhỏ.

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

Từ v1.1.0, app nhận từng câu dịch hoàn chỉnh và phát các đoạn audio
ngay khi sẵn sàng, đồng thời tổng hợp phần tiếp theo. Ưu tiên **TTS only** hoặc
overlay **Banner** cho đường xử lý này; chế độ overlay in-place vẫn dùng đường
đọc đã đệm. App vẫn chờ OCR xác nhận chữ ổn định, giữ nguyên giọng hiện tại và
đọc hết câu đang phát. Chỉ hội thoại chờ mới nhất được giữ lại.

Với game tiếng Anh, **English + Only selected box + TTS only** là cấu hình
đã đo để ưu tiên TTFA. Model cần nạp xong trước khi đo; chữ còn đang chạy và
câu đọc trước chưa kết thúc vẫn làm tăng thời gian chờ.

Từ v1.1.2, app đối chiếu ảnh chữ ngay từ trước lần đọc đầu
để OCR dao động hoặc mất kết quả tạm thời không làm TTS chờ mãi. Bộ lọc cũng
nhận mũi tên tiếp tục màu trắng. Chỉ tái dùng chữ khi ảnh xác nhận glyph không
đổi; số, phủ định và chữ mới thực sự vẫn được xử lý. Log phát audio ghi thiết
bị đầu ra cùng mức peak/RMS để phân biệt chờ OCR với vấn đề âm thanh.

Câu giống hệt ở cảnh mới được đọc lại sau khi vùng chữ có tọa độ thực sự
trống liên tục ít nhất 1,5 giây. Chớp ảnh ngắn hoặc OCR mất kết quả trên ảnh
còn chữ không xóa bộ nhớ chống lặp; câu đang phát không bị ngắt.

### Giọng theo nhân vật (thử nghiệm từ v1.2.0)

1. Chọn cửa sổ game trong mục **Capture**, bắt đầu capture, rồi mở **Character voices…** ở đầu app.
2. Chọn **Select name area…**, khoanh riêng nhãn tên người đang nói và nhấn **OK**.
3. Nhập tên nhân vật đúng như game hiển thị, chọn **Voice preset**, rồi **Add / update**.
   Dùng **Test selected voice** để nghe thử preset. Gán một lần cho mỗi tên; không phân biệt hoa/thường.
4. Bật **Automatically select a voice from the speaker name**, rồi **OK** để lưu theo game.

Khi đang capture, hộp thoại chỉnh cửa sổ đang được chụp. Khi đã dừng,
hộp thoại chỉnh nguồn đang chọn trong danh sách và cho phép sửa các giọng đã gán.

App đọc tên bằng cùng engine OCR offline trên worker; không gọi thêm LLM và
không đoán giới tính từ hình ảnh. Vùng tên được tách khỏi phần dịch, kể cả khi
nó nằm ngoài box hội thoại hoặc trong vùng exclude. Tên chưa gán/lời dẫn dùng
giọng mặc định và mẫu clone hiện có; nhân vật đã gán dùng preset trong cùng model.
Khi đổi model giọng, cần chọn lại preset tương ứng. Giọng được chốt cho từng câu;
đổi tên nhân vật không cắt câu đang đọc. Game cần hiển thị nhãn tên rõ ràng.

## Remote Desktop trên Windows

Từ v1.1.2, app tự nhận phiên Remote Desktop và chụp riêng
cửa sổ game bằng PrintWindow. Khi quay lại máy, app tự tạo lại Windows Graphics
Capture. Việc chuyển phiên và thử lại chạy nền; không cần Stop/Start capture.
Ảnh chụp chỉ chứa cửa sổ game, tránh đọc ngược bản dịch overlay vào OCR.

Giữ game mở, không minimize; ưu tiên windowed/borderless. Nếu Windows tạm ngừng
cấp ảnh khi đổi phiên hoặc độ phân giải, thanh trạng thái báo **Waiting for
window frames** và tự tiếp tục khi có ảnh. Game dùng cách render không hỗ trợ
PrintWindow vẫn có thể trả ảnh đen; không phải mọi game/driver đều được hỗ trợ.
Khi phiên Windows ngắt kết nối, app chờ phiên hoạt động trước khi mở capture
native và tự tiếp tục sau khi kết nối lại. Bản v1.1.1 chưa có phần sửa này.

Giọng TTS theo đầu ra âm thanh mặc định của Windows. Khi kết nối Remote
Desktop có **Remote Audio**, các câu tiếp theo tự chuyển sang thiết bị đó;
khi trở về console, app theo đầu ra mặc định mới. Câu đang phát được giữ
nguyên tới hết để tránh cắt tiếng. Từ v1.1.3, app cập nhật danh sách thiết bị
trước câu tiếp theo khi đầu ra đổi. WASAPI phát qua callback với bộ đệm có
giới hạn để tránh nghẽn ghi âm thanh sau khi đổi phiên; nếu callback ngừng
hoạt động, app báo lỗi và làm mới thiết bị ở lần phát sau.

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
Prompt mặc định giữ sát ý nghĩa, cảm xúc, cách xưng hô và mức độ thô tục của
nguyên bản; không tự làm nhẹ, tăng nặng hay thêm chi tiết. Prompt tự nhập vẫn
được ưu tiên nếu bạn đã cấu hình riêng.

## Cập nhật ứng dụng

Để lên **v1.2.0**, dùng tab **Updates**, hoặc tải
`Ludivoxa-1.2.0-App-Update.exe` từ
[bản phát hành ổn định](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.2.0)
và chọn thư mục Ludivoxa đang cài. Gói cập nhật giữ model/runtime/cấu hình.
Người dùng mới cần cài bộ offline v1.1.1 ở đầu hướng dẫn trước.
Các bản v1.1.1, v1.1.2-rc.1, v1.1.2 và v1.1.3 đều cập nhật được qua tab Updates.

Từ v1.1.0, mở **Updates** ở thanh bên:

1. Chọn **Check for updates** để kiểm tra release ổn định mới hơn trên GitHub.
2. Đọc release notes và dung lượng, rồi chọn **Download update**. Có thể hủy
   tải; app vẫn dịch/đọc bình thường trong lúc kiểm tra hoặc tải.
3. Khi file được xác minh SHA-256, chọn **Install and restart** lúc thuận tiện.
   App đóng, bộ cài cập nhật đúng thư mục hiện tại rồi mở lại ứng dụng.

**Check for updates when Ludivoxa starts** bật kiểm tra tự động và thông báo
trong UI; tải/cài vẫn do bạn chọn. Mặc định tùy chọn này tắt để giữ cách chạy
offline. Tính năng cập nhật dùng mạng GitHub, độc lập với OCR/LLM/TTS local.

Nếu release có gói **App-Update**, app ưu tiên gói đó: không tải lại hay ghi đè
model OCR/TTS, kho model LLM, runtime Ollama hoặc `config.yml`. Gói này chỉ cài
vào một thư mục Ludivoxa đã có `Interpreter.exe` và `config.yml`. Release cũ
chỉ có bộ cài offline đầy đủ vẫn được hỗ trợ; khi đó phải tải cả EXE và BIN,
và UI hiển thị toàn bộ dung lượng trước khi tải.

Repo Ludivoxa đã public. Kiểm tra và tải cập nhật không cần nhập key, token
hay đăng nhập GitHub. App không đọc thông tin đăng nhập Git hoặc Windows
Credential Manager. Nếu không truy cập được release hoặc bị giới hạn lượt
truy cập GitHub, app sẽ báo lỗi để thử lại sau, không xem là “đã mới nhất”.

Tải cache nằm ở `%USERPROFILE%/.interpreter/updates`; nếu cài đặt lỗi, xem
`install.log` trong thư mục phiên bản đó. Cài tự động dành cho EXE Windows;
bản chạy từ source cần dùng bộ cài hoặc cập nhật mã nguồn thủ công. Bộ cài
hiện vẫn chưa ký số. Bản v1.0.0 đã phát hành trước đó chưa có trang Updates;
cần chạy gói `Ludivoxa-1.1.1-App-Update.exe` thủ công một lần vào thư mục app cũ.

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
