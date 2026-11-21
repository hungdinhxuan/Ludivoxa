# Hướng dẫn dùng Ludivoxa để dịch game offline trên Windows

Dùng Ludivoxa để đọc hội thoại tiếng Anh, Nhật, Trung và Hàn trên màn hình,
dịch qua Ollama và hiển thị phụ đề hoặc phát giọng đọc tiếng Việt. Hướng dẫn
này dành cho bộ cài Windows x64 và có cách kết nối máy chủ dịch trong LAN.

[Cài đặt Windows](#cài-bản-windows-offline) · [Khoanh ô hội thoại](#dịch-hội-thoại) ·
[Phụ đề và giọng đọc](#overlay-và-giọng-đọc) · [Ollama local hoặc LAN](#ollama-local-và-gpu-server-lan) ·
[Xử lý sự cố](#xử-lý-sự-cố)

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
Python, Ollama, model dịch, OCR Anh/Nhật và TTS tiếng Việt đã có sẵn. Bộ cài
mặc định cài theo tài khoản Windows đang dùng. Sau khi cài bản offline, mở
**Updates → Check for updates** và áp dụng gói cập nhật hiện tại.
[Bản phát hành ổn định mới nhất](https://github.com/hungdinhxuan/Ludivoxa/releases/latest)

## Dịch hội thoại

1. Mở game ở chế độ cửa sổ hoặc borderless. Trong Ludivoxa, chọn **Refresh**
   rồi chọn đúng cửa sổ game.
2. Chờ OCR, translation và voice sẵn sàng, rồi chọn **Start Capture**.
3. Mở **Configure OCR** → **Scan: Only selected boxes**.
4. Kéo khoanh từng vùng hội thoại, rồi nhấn **OK**.

Từ v1.1.3, **Configure OCR** nằm trên thanh đầu cửa sổ app. Trong dialog,
**OK / Cancel / Clear** luôn ở trên cùng; nội dung bên dưới có thể cuộn và
preview tự co theo kích thước cửa sổ để dùng trên màn hình remote nhỏ.

Kéo trên vùng trống để thêm ô xanh. Bấm chọn một ô để di chuyển, kéo góc,
chỉnh phần trăm hoặc dùng **Remove Selected Box**. **Use bottom dialogue box**
thêm một ô ở phía dưới. Các ô lưu riêng theo cửa sổ và dùng tọa độ theo tỷ lệ ảnh.

Chỉ phần bên trong các ô được đưa vào OCR. Trong lúc chỉnh, app chỉ xem trước kết quả OCR,
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
- Từ v1.6.0, game tiếng Trung giản thể hoặc phồn thể dùng **Chinese** và **PaddleOCR**.
  App dùng model `PP-OCRv5_mobile_rec`. Chọn **Chinese** để Ollama nhận đúng
  ngôn ngữ khi chữ Hán có thể được hiểu là tiếng Nhật.
- Game tiếng Hàn: chọn **Korean** và **PaddleOCR**. App dùng model
  `korean_PP-OCRv5_mobile_rec`. Với bản chạy từ mã nguồn, chuẩn bị model
  theo [hướng dẫn phát triển](DEVELOPMENT.md#provision-models-explicitly).
  Có thể lưu **Chinese** hoặc **Korean** theo từng game trong **Game profile…**.
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
Thanh **Speech volume** chỉnh riêng âm lượng giọng đọc từ 0% đến 100%. Mức mới
áp dụng từ lượt phát tiếp theo, kể cả **Test voice** và **Replay** trong History.
Với Nano, **Clone from audio** nhận một file ghi âm sạch trên máy; không cần
transcript. Để trống thì dùng preset. File tham chiếu không được upload.

Trong **Overlay**, bật **Clear translation when dialogue changes** để xóa
bản dịch cũ khi OCR nhận ra câu mới. Nếu khung hình mới đã chờ xử lý khi bản
dịch cũ về, app so sánh vùng chữ đã nhận dạng để tránh hiện đè bản dịch cũ.
Các câu đã ổn định vẫn được lưu trong History. Tùy chọn áp dụng cho cả Banner
và In-place, mặc định tắt.

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

### Lịch sử hội thoại

Mở **History** ở thanh bên để xem câu gốc và bản dịch. App giữ tối đa 100 mục
đã dịch trong bộ nhớ cho đến khi đóng app. Lịch sử vẫn xem được sau khi dừng
capture.

Trên Windows, khi đang capture cửa sổ game, nút **History** bán trong suốt nằm
ở góc trên bên phải. Bấm để mở cùng lịch sử trong cửa sổ nổi, rồi dùng **Esc**
hoặc bấm lại vào game để đóng cửa sổ nổi. Nút cũng dùng được ở chế độ **TTS only**. Tắt
tùy chọn **Show History button over the game** trong trang **Overlay** nếu muốn
ẩn nút.

- **Replay** đọc lại bản dịch tiếng Việt với giọng đã dùng cho câu đó. Phát
  thủ công cũng dùng được ở chế độ **Overlay only**. Chờ câu đang phát kết thúc
  trước khi bấm nghe lại.
- **Copy translation** sao chép bản dịch của mục đang chọn.
- **Retranslate** dịch lại câu gốc bằng engine và thiết lập dịch hiện tại.
  Kết quả cập nhật mục lịch sử đã chọn. Tên và giới tính người nói vẫn được
  gửi nếu dùng LLM. Nếu game có prompt riêng, app dùng prompt hiện tại của
  game đó.

### Hồ sơ theo game

Chọn nguồn trong **Play**, rồi bấm **Game profile…** trên đầu ứng dụng. Khi
đang capture, nút mở cấu hình của cửa sổ đang chụp. Mỗi hồ sơ lưu theo đúng tên
cửa sổ, cùng cách lưu vùng OCR, ngưỡng tin cậy và giọng theo nhân vật.

- **Source language** đặt riêng ngôn ngữ của game. Chọn **Use Engines setting**
  để dùng ngôn ngữ mặc định.
- **LLM system prompt** ghi đè prompt ở **Engines** cho game đó. Prompt có thể
  dùng `{source_language}` và `{target_language}`. App đổi bộ nhớ hội thoại
  khi chuyển game để giữ tên và ngữ cảnh đúng với nguồn đang dịch.
- **Ignore OCR phrases** nhận một cụm hoàn chỉnh mỗi dòng, ví dụ `Press START`.
  Bộ lọc bỏ qua khác biệt về chữ hoa, chữ thường và khoảng trắng. Một cụm dài
  có chứa cụm đã lọc vẫn được dịch nếu không trùng toàn bộ dòng OCR.

Chỉnh vùng quét và ngưỡng tin cậy bằng **Configure OCR**, và chỉnh vùng tên
cùng giọng nhân vật bằng **Character voices…**. Các phiên Wayland dùng chung
hồ sơ **Wayland Capture** vì cổng chọn màn hình không cung cấp tên cửa sổ.

### Chẩn đoán capture và âm thanh

Mở **Diagnostics** để xem capture có nhận frame hay không, backend capture
đang cấp frame, thời gian OCR/dịch gần nhất, số vùng bị lọc và trạng thái TTS.
Trên Windows có thêm trạng thái Remote Desktop, tên thiết bị âm thanh mặc
định hiện tại, thiết bị phát ở lượt gần nhất và mã lỗi PortAudio nếu có.
**Copy report** sao chép thông tin kỹ thuật để báo lỗi. Bản sao bỏ tên thiết
bị, tên cửa sổ, nội dung hội thoại và cài đặt endpoint.

### Giọng theo nhân vật (thử nghiệm từ v1.2.0)

1. Chọn cửa sổ game trong mục **Capture**, bắt đầu capture, rồi mở **Character voices…** ở đầu app.
2. Chọn **Select name area…**, khoanh riêng nhãn tên người đang nói và nhấn **OK**.
3. Nhập tên như game hiển thị, chọn **Voice preset** và **Character gender**, rồi **Add / update**.
   **From preset** lấy nhãn của giọng Nano. Chọn giới tính trực tiếp nếu nhân vật
   khác nhãn giọng, hoặc dùng **Default voice** để chỉ gán giới tính.
4. Bật **Automatically select a voice from the speaker name**, rồi **OK** để lưu theo game.

Khi đang capture, hộp thoại chỉnh cửa sổ đang được chụp. Khi đã dừng,
hộp thoại chỉnh nguồn đang chọn trong danh sách và cho phép sửa các giọng đã gán.

App đọc tên bằng cùng engine OCR offline trên worker; không gọi thêm LLM và
không đoán giới tính từ hình ảnh. Vùng tên được tách khỏi phần dịch, kể cả khi
nó nằm ngoài box hội thoại hoặc trong vùng exclude. Tên chưa gán/lời dẫn dùng
giọng mặc định và mẫu clone hiện có; nhân vật đã gán dùng preset trong cùng model.
Khi đổi model giọng, cần chọn lại preset tương ứng. Giọng được chốt cho từng câu;
đổi tên nhân vật không cắt câu đang đọc. Game cần hiển thị nhãn tên rõ ràng.

Tên và giới tính của người đang nói được gửi trong chính lượt dịch để LLM
chọn cách xưng hô theo ngữ cảnh. Khi chưa rõ, LLM có thể dựa vào hội thoại
hoặc dùng cách xưng hô trung tính. TTS đọc phần lời thoại và giữ tên được nhắc
trong câu. Một ô tên xác định người nói hiện tại cho
các ô hội thoại. [Nhãn giọng Nano của nhà phát hành](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Nano/blob/main/README.md#-preset-voices-11)

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

Mở **Updates** ở thanh bên để nâng cấp bản đang cài. Bạn cũng có thể tải gói
**App-Update.exe** và chọn thư mục Ludivoxa hiện tại khi chạy bộ cài.
[Bản phát hành ổn định mới nhất](https://github.com/hungdinhxuan/Ludivoxa/releases/latest)

1. Chọn **Check for updates** để kiểm tra release ổn định mới hơn trên GitHub.
2. Xem dung lượng rồi chọn **Download smaller update** hoặc **Download update**.
   App vẫn dịch và đọc trong lúc kiểm tra hoặc tải.
3. Sau khi xác minh SHA-256, chọn **Install and restart**. Bộ cài nâng cấp
   đúng thư mục rồi mở lại ứng dụng.

**Check for updates when Ludivoxa starts** bật kiểm tra tự động và thông báo
trong UI; tải/cài vẫn do bạn chọn. Mặc định tùy chọn này tắt để giữ cách chạy
offline. Tính năng cập nhật dùng mạng GitHub, độc lập với OCR/LLM/TTS local.

Updater từ v1.4.0 ưu tiên gói vá nhỏ khi bản phát hành có gói phù hợp và mã băm
các tệp ứng dụng đang cài khớp. Nếu xác minh thất bại, tab Updates hiển thị
dung lượng gói chuẩn để bạn chọn tải. Cả gói vá và **App-Update** đều giữ
config, model và Ollama đã cài. Chọn thư mục có `Interpreter.exe` và
`config.yml` khi cập nhật thủ công. Nếu bản phát hành chỉ có bộ offline đầy
đủ, updater sẽ hiển thị tổng dung lượng của EXE và các tệp BIN.

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
