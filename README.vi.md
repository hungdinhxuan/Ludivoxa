# Ludivoxa

Ứng dụng OCR game offline, dịch bằng LLM local và tự đọc tiếng Việt.

[English](README.md) · [Hướng dẫn](docs/USER_GUIDE.md) ·
[Changelog](CHANGELOG.md) · [Tải bản phát hành](https://github.com/hungdinhxuan/Ludivoxa/releases)

Ludivoxa được phát triển từ [Interpreter](https://github.com/bquenin/interpreter)
của qube/bquenin, giữ pipeline và giao diện Qt hiện có. Dự án này bắt đầu bằng
**v1.0.0**, độc lập với phiên bản của upstream.

**Bản ổn định mới nhất [v1.3.1](docs/releases/v1.3.1.md)** sửa lỗi đọc lặp khi OCR
tên nhân vật dao động. Tên trong vùng OCR riêng dùng để chọn giọng và giúp LLM
chọn cách xưng hô. Bản đã cài có thể nâng cấp qua **Updates**.
Người dùng mới cài bộ offline v1.1.1 trước rồi cập nhật app.

## Có gì trong v1.3.0?

Mở **Configure OCR** để chọn nhiều ô hội thoại. Trong **Character voices…**,
khoanh vùng tên rồi gán giọng và giới tính cho từng nhân vật. LLM dùng thông tin
người nói để chọn cách xưng hô phù hợp. [Ghi chú bản phát hành](docs/releases/v1.3.0.md)

- OCR Anh/Nhật offline, phát hiện ngôn ngữ hoặc chọn thủ công.
- Khoanh nhiều vùng hội thoại để chỉ quét phần cần dịch. Vùng chọn lưu riêng
  theo game. Bạn vẫn có thể chuyển sang quét toàn cửa sổ hoặc loại trừ.
- Ollama local hoặc OpenAI-compatible/GPU server LAN; giữ context, prompt tùy
  chỉnh, mặc định dịch sang tiếng Việt và không tự fallback sang cloud.
- Overlay, TTS hoặc cả hai; tự đọc mỗi bản dịch ổn định mới.
- Nano có 11 preset, clone từ file ghi âm local nếu bạn cung cấp; đồng thời
  giữ 67 lựa chọn Piper. Không kèm mẫu clone giọng cá nhân.
- Xử lý nền, cache, chống đọc lặp; dịch từng câu và phát Nano theo từng đoạn
  audio để giảm thời gian chờ tiếng đầu tiên.
- Tab Updates: kiểm tra bản mới, tải nền và cài đặt/khởi động lại; không cần
  GitHub key. Có tùy chọn tự kiểm tra khi mở app.
- Đóng app cuối cùng tự tắt Ollama riêng cùng model runners; giữ nguyên server
  local/LAN dùng chung. Mở lại app phải tải model lại.

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

## Cài Windows offline

Từ [bộ offline v1.1.1](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.1.1), tải EXE và
**cả ba file BIN** của cùng phiên bản, đặt chung thư mục rồi chạy EXE. Có
`SHA256SUMS.txt` để kiểm tra. Tổng khoảng 4,9 GB; app cài xong khoảng 6,4 GB.
Python, Ollama, OCR/TTS models và HauhauCS Qwen3 4B Aggressive Q4_K_M đã có sẵn.
Bản build chưa ký số. Các file Source code tự động của GitHub không chứa model.

Sau đó vào tab **Updates** để cập nhật v1.3.1 (cũng hỗ trợ từ v1.1.2rc1).
Nếu dùng v1.0.0 hoặc muốn cập nhật thủ công, tải **Ludivoxa-1.3.1-App-Update.exe**
từ [v1.3.1](https://github.com/hungdinhxuan/Ludivoxa/releases/tag/v1.3.1), chạy
và chọn đúng thư mục app đang dùng. Gói nhỏ giữ nguyên cấu hình, Ollama và
model; v1.0.0 chưa có updater nên cần nâng cấp thủ công một lần.

Mở game → chọn cửa sổ → **Start Capture → Configure OCR → Only selected box**,
khoanh hội thoại rồi **OK**. Có thể di chuyển, đổi kích thước hoặc nhập phần
trăm tọa độ. Chọn **Overlay only**, **TTS only** hoặc **Overlay + TTS**.

- Game Anh: **English**, model `en_PP-OCRv5_mobile_rec`.
- Game Nhật hoặc hỗn hợp: **Japanese/Auto**, model `PP-OCRv5_mobile_rec`.
- **F8**: ẩn/hiện overlay. **Stop reading**: dừng giọng.
- Ollama riêng của app: `http://127.0.0.1:11435`.
- TTS đi kèm chỉ đọc tiếng Việt; target khác nên dùng Overlay only.

Tên EXE `Interpreter.exe`, namespace Python và vị trí cấu hình cũ được giữ để
tương thích. Bộ cài nâng cấp giữ cấu hình. Source developer có thể dùng alias
`ludivoxa`; các script cài legacy ở thư mục gốc vẫn dành cho upstream PyPI.

Xem [hướng dẫn đầy đủ](docs/USER_GUIDE.md), [build từ source](docs/DEVELOPMENT.md),
[phát hành theo tag](docs/RELEASING.md), [số đo và giới hạn](docs/PERFORMANCE.md).
OCR/dịch/TTS có độ trễ riêng; đoạn dài vẫn có thể chậm. Các số đo ảnh game đã
ghi không phải cam kết FPS khi chơi trực tiếp.

Mã ứng dụng dùng [MIT](LICENSE); các dependency/model giữ giấy phép riêng.
Xem [ghi công và third-party notices](NOTICE.md).
