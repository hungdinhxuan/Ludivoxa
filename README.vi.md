# Ludivoxa — Phần mềm dịch game offline và đọc tiếng Việt

**Ludivoxa** là ứng dụng mã nguồn mở để dịch chữ trên màn hình game PC. App
dùng OCR offline để đọc hội thoại tiếng Anh hoặc tiếng Nhật, dịch sang tiếng
Việt bằng LLM local, rồi hiện phụ đề trên game hoặc đọc thành tiếng. Bạn có
thể dùng Ludivoxa cho visual novel, game nhập vai và các game có hội thoại
hiện trên màn hình.

[Tải cho Windows](#cài-windows-offline) · [Hướng dẫn dịch game](docs/USER_GUIDE.md) ·
[Sơ đồ kiến trúc](README.md#architecture) · [English](README.md)

**Bản ổn định v1.5.0** có lịch sử hội thoại, hồ sơ theo game, Diagnostics
và âm lượng TTS riêng. [Ghi chú v1.5.0](docs/releases/v1.5.0.md)

## Tính năng dịch game

### OCR offline cho game tiếng Anh và tiếng Nhật

Dùng PaddleOCR để quét toàn cửa sổ hoặc chỉ quét các ô hội thoại đã khoanh.
Có thể chọn nhiều ô, lưu riêng theo game và lọc vùng không cần dịch. Chọn
**English** để ưu tiên tốc độ cho game tiếng Anh, **Japanese** cho game tiếng
Nhật hoặc **Auto** khi hội thoại trộn hai ngôn ngữ.

### Dịch bằng Ollama local hoặc máy chủ GPU trong LAN

Ludivoxa mặc định dịch sang tiếng Việt và cho phép chọn ngôn ngữ đích khác.
Dùng Ollama đi kèm, Ollama trên máy bạn hoặc endpoint tương thích OpenAI
trong mạng LAN. App dùng ngữ cảnh hội thoại và prompt riêng để giữ tên,
thuật ngữ và cách xưng hô giữa các câu.

### Phụ đề trên game và giọng đọc tiếng Việt

Hiện bản dịch ở banner hoặc ngay tại vị trí chữ gốc, nghe TTS, hoặc dùng cả
hai. Bộ Windows đầy đủ có 11 preset VieNeu Nano, 67 lựa chọn Piper và tùy
chọn clone giọng từ đoạn thu âm trên máy. Khoanh riêng vùng tên để gán giọng
theo nhân vật. TTS đọc lời thoại và có thanh âm lượng riêng. Có thể chọn
ẩn phụ đề cũ trong lúc dịch câu mới.

### Lịch sử hội thoại, hồ sơ theo game và chẩn đoán

Mở **History** để nghe lại, sao chép hoặc dịch lại câu trước. **Game profile**
lưu ngôn ngữ nguồn, prompt và cụm từ OCR cần bỏ qua. **Diagnostics** cho
biết trạng thái chụp hình, dịch và phát âm thanh, hỗ trợ kiểm tra lỗi khi
dùng Remote Desktop.

## Cài Windows offline

Bộ cài **Windows x64** có sẵn Python, Ollama, HauhauCS Qwen3 4B Aggressive
Q4_K_M, model OCR Anh/Nhật và giọng đọc tiếng Việt. Bộ đầy đủ tải khoảng
**4,9 GB** và dùng khoảng **6,4 GB** sau khi cài.

1. Tải EXE và **cả ba tệp BIN** từ
   [bộ cài offline v1.1.1](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.1.1).
2. Đối chiếu `SHA256SUMS.txt`, đặt cả bốn tệp chung thư mục, rồi chạy EXE.
3. Mở **Updates → Check for updates** để cài phiên bản ổn định hiện tại.

**Máy đã có Ludivoxa?** Dùng **Updates** hoặc tải gói **App-Update.exe** từ
[bản phát hành ổn định mới nhất](https://github.com/hungdinhxuan/Ludivoxa/releases/latest)
và chọn thư mục đang cài. Từ v1.4.0, updater có thể tải gói vá nhỏ khi tệp
cài đặt khớp. Gói cập nhật giữ model, Ollama và `config.yml`. Các tệp trong
mục **Assets** tải được mà không cần đăng nhập GitHub. Bộ cài chưa ký số.

## Cách dịch hội thoại trong game

1. Mở game ở chế độ cửa sổ hoặc borderless, chọn cửa sổ trong Ludivoxa rồi
   bấm **Start Capture**.
2. Trong **Configure OCR**, chọn **Only selected boxes**, khoanh các ô hội
   thoại rồi bấm **OK**.
3. Trong **Engines**, chọn **English** với `en_PP-OCRv5_mobile_rec` cho game
   tiếng Anh. Chọn **Japanese** hoặc **Auto** với `PP-OCRv5_mobile_rec` cho
   game tiếng Nhật hoặc hội thoại Anh/Nhật.
4. Chọn **Overlay only**, **TTS only** hoặc **Overlay + TTS**. Dùng **F8** để
   ẩn/hiện phụ đề và **Stop reading** để dừng đọc.

Sau khi cài, cấu hình offline chạy OCR, dịch và đọc ngay trên máy. Ollama đi
kèm dùng `http://127.0.0.1:11435`. Nếu dùng máy chủ LAN, đặt địa chỉ trong
**Engines**. App dùng máy chủ đã chọn, không tự chuyển sang dịch cloud. Nếu
dịch sang ngôn ngữ khác, dùng **Overlay only** vì giọng đi kèm đọc tiếng Việt.
[Cấu hình Ollama local hoặc LAN](docs/USER_GUIDE.md#ollama-local-và-gpu-server-lan)

## Benchmark TTFA theo phiên bản

**TTFA (time to first audio)** là thời gian từ khi có sẵn một khung hình hoàn
chỉnh đến khi nhận được tiếng nói tương ứng qua Windows audio loopback, gồm
OCR, chờ chữ ổn định, dịch, tổng hợp giọng và độ trễ đầu ra. Số liệu dưới đây
là **trung vị của ba lần đo khi model đã nóng cho mỗi trường hợp**, tính bằng
mili giây; càng thấp càng nhanh.

| Phiên bản / cấu hình đo | Hội thoại game ngắn | Hai câu tiếng Anh (mẫu tổng hợp) | Đoạn game dài |
| --- | ---: | ---: | ---: |
| v1.0.0 — đệm toàn bộ audio | 1.413,6 ms | 1.743,6 ms | 4.465,2 ms |
| v1.1.0 — cấu hình streaming¹ | 912,6 ms | 1.013,6 ms | 1.436,9 ms |
| Mức giảm trong phép so sánh cũ với v1.0.0 | **35,4%** | **41,9%** | **67,8%** |
| v1.1.0 — đúng tag phát hành² | 896,1 ms | 1.038,6 ms | 1.439,5 ms |
| v1.1.1 — đúng tag phát hành² | 888,0 ms | 971,2 ms | 1.433,6 ms |
| v1.1.2-rc.1 — đúng tag phát hành² | 866,5 ms | 849,3 ms | 1.364,3 ms |
| v1.1.2 stable — đúng tag phát hành² | **867,7 ms** | **966,3 ms** | **1.355,9 ms** |
| v1.1.3 stable | Chưa đo | Chưa đo | Chưa đo |
| v1.2.0 stable | Phép đo riêng bên dưới | Chưa đo | Chưa đo |
| v1.3.0 stable | Phát lại khung riêng | Phát lại khung riêng | Chưa đo |
| v1.3.1 stable | Mẫu ngắn tổng hợp bên dưới | **1.014,42 ms** qua phát lại khung | Chưa đo |
| v1.4.0 stable | Mẫu tổng hợp hai câu | **1.108,41 ms** qua phát lại khung | Chưa đo |
| v1.5.0 stable | Mẫu tổng hợp hai câu | **914,23 ms** qua phát lại khung | Chưa đo |

<details>
<summary>Phương pháp đo và kết quả các bản trước</summary>

Máy đo: Windows 11, Core i7-13700F, RTX 4090 24 GB. Hai lượt dùng cùng ảnh
hội thoại đã khoanh box, OCR Anh → dịch Việt, HauhauCS Qwen3 4B Q4_K_M local,
ba cặp context cố định và Nano speaker 7, tám bước/hai luồng CPU. Không có
câu trước đang chờ đọc. So sánh gồm cả chuyển đầu ra từ đệm/MME sang
streaming/WASAPI trên cùng thiết bị loa.

¹ Số streaming được đo trong giai đoạn phát triển v1.1.0, trước thay đổi
prompt/updater cuối cùng; chưa đo lại riêng bộ cài đã phát hành. Phép đo chưa
tính thời gian tải model, chờ capture, chữ chạy trong game hoặc chờ đọc hết
câu trước. Đây không phải phép đo FPS game, độ trễ âm thanh qua loa thực hay p95.

² Đo ngày 16/11/2026 bằng mã nguồn chính xác từng tag, trong phiên console
Windows hoạt động và đã tạm dừng âm thanh khác: **36 lượt loopback hợp lệ**,
ba lượt cho mỗi đầu vào/tag. Cùng streaming/WASAPI shared 48 kHz, cấu hình
Qwen/Nano, context Ollama 2048 và prompt mặc định từng tag. Hệ số khớp waveform
0,958–1,000. Model đã nóng, frame đã có đầy đủ chữ; chênh lệch nhỏ từ ba lượt
không đủ kết luận bản nào luôn nhanh hơn. Dòng phần trăm chỉ áp dụng cho hai
cấu hình lịch sử đầu bảng. Xem [phương pháp đo theo tag](docs/PERFORMANCE.md#tagged-version-loopback-ttfa)
và [từng mẫu thời gian](docs/benchmarks/ttfa.json). Chưa benchmark riêng bộ cài EXE.

**Đo lại đúng các tag ngày 16/11/2026 — từ frame tới callback PCM của driver.**
Ba bản còn thiếu đã được đo cùng ảnh, cấu hình Qwen/Nano và đầu ra WDM-KS.
Mỗi ô là trung vị ba lượt khi model đã nóng; đo thêm v1.1.0 trên cùng đường
âm thanh để đối chứng.

| Mã nguồn theo tag / WDM-KS | Hội thoại game ngắn | Hai câu tiếng Anh (mẫu tổng hợp) | Đoạn game dài |
| --- | ---: | ---: | ---: |
| v1.1.0 — đối chứng | 3.914,3 ms | 4.448,3 ms | 15.644,1 ms |
| v1.1.1 | 3.515,8 ms | 4.450,3 ms | 15.404,5 ms |
| v1.1.2-rc.1 | 3.984,6 ms | 4.240,7 ms | 15.543,2 ms |
| v1.1.2 stable | 3.987,0 ms | 4.332,1 ms | 15.585,3 ms |

**Đây là mốc PCM tới driver, chưa phải TTFA loopback.** Phiên Windows đang
ngắt kết nối nên không có WASAPI loopback. Đường WDM-KS hiện phải đệm toàn bộ
câu đã tổng hợp, gồm mọi đoạn streaming, rồi mới phát; buffer thiết bị khoảng
61 ms chỉ là một phần thời gian chờ. Bản v1.1.0 đối chứng cũng chờ lâu trên
đường này. Không so trực tiếp bảng này với bảng streaming/WASAPI phía trên,
hoặc kết luận chênh lệch nhỏ giữa các bản từ ba lượt đo. TTFA loopback trong
phiên console hoạt động đã có ở bảng đầu; giữ bảng WDM-KS cũ để tham khảo
riêng đường đầu ra khi ngắt kết nối. Xem
[phương pháp và mốc yêu cầu phát](docs/PERFORMANCE.md#tagged-version-native-callback-audit)
và [đủ 36 mẫu thời gian](docs/benchmarks/ttfa-native-callback.json).

Chạy lại OCR thật trên 10 ảnh GBA đã lưu cho kết quả **10 → 1 yêu cầu đọc**,
với thời gian phát lại được kiểm soát, bản dịch mô phỏng và không phát giọng.
Đây là kiểm chứng chống lặp, không phải TTFA. Chữ đang hiện dần được chờ thích
ứng; không dùng số TTFA cũ hoặc phép phát lại này để đại diện cho v1.1.1.

Xem [phương pháp và cách đo lại](docs/PERFORMANCE.md#v110-streaming-profile)
và [các mẫu thời gian công khai](docs/benchmarks/ttfa.json). Mỗi bản mới sẽ có
dòng riêng khi có số đo tương đương; các cách quan sát khác nhau được ghi riêng.

**Đo riêng v1.2.0 trong game.** Bật giọng nhân vật, dùng Gemma 4 E4B Q4_K_M
và Nano TTS. Phát lại 4 câu đã hiện đủ chữ với vùng exclude cho kết quả
trung vị **1,23 giây** qua WASAPI (12 lượt). Khi so 6 cặp trên cùng một câu ngắn,
quét riêng ô hội thoại giảm trung vị **72 ms (7,5%)**, với TTFA trung vị
**0,82 giây**. Các phép đo dùng mã bản ứng viên và khung hình đã lưu, nên chưa
tính thời gian chữ chạy và chờ câu trước phát xong.

**Phát lại khung với v1.3.0.** Cùng Gemma và VieNeu, TTFA trung vị của bốn
cặp khung tổng hợp là **1,20 giây khi kiểm tra theo timer** và **0,95 giây
khi nhận khung qua thông báo**, giảm trung vị **209 ms theo cặp**. Đo tiếng
đầu tiên qua WASAPI. Xem [từng lượt và phạm vi đo](docs/PERFORMANCE.md#v130-windows-frame-replay).

**Phát lại khung với v1.3.1.** TTFA trung vị là **897,24 ms** với mẫu hội thoại
ngắn và **1.014,42 ms** với hai câu tiếng Anh, mỗi trường hợp đo ba lượt qua
WASAPI. [Từng lượt và cấu hình đo](docs/PERFORMANCE.md#v131-windows-frame-replay)

**Phát lại khung với v1.4.0.** TTFA trung vị là **1.028,81 ms** với mẫu thoại
ngắn và **1.108,41 ms** với mẫu hai câu. [Từng lượt và điều kiện đo](docs/PERFORMANCE.md#v140-windows-frame-replay)

**Phát lại khung với v1.5.0.** TTFA trung vị là **910,24 ms** với mẫu thoại
ngắn và **914,23 ms** với mẫu hai câu. [Từng lượt và điều kiện đo](docs/PERFORMANCE.md#v150-windows-frame-replay)

</details>

## Tài liệu và mã nguồn

- [Hướng dẫn sử dụng và xử lý lỗi](docs/USER_GUIDE.md)
- [Cách build Ludivoxa từ mã nguồn](docs/DEVELOPMENT.md)
- [Nhật ký thay đổi](CHANGELOG.md) và [các bản phát hành](https://github.com/hungdinhxuan/Ludivoxa/releases)

Ludivoxa phát triển từ [Interpreter](https://github.com/bquenin/interpreter).
Mã ứng dụng dùng giấy phép MIT. Thư viện và model có giấy phép riêng.
[Ghi công và giấy phép](NOTICE.md)
