# Ludivoxa

Ứng dụng OCR game offline, dịch bằng LLM local và tự đọc tiếng Việt.

[English](README.md) · [Hướng dẫn](docs/USER_GUIDE.md) ·
[Changelog](CHANGELOG.md) · [Tải bản phát hành](https://github.com/hungdinhxuan/Ludivoxa/releases)

Ludivoxa được phát triển từ [Interpreter](https://github.com/bquenin/interpreter)
của qube/bquenin, giữ pipeline và giao diện Qt hiện có. Dự án này bắt đầu bằng
**v1.0.0**, độc lập với phiên bản của upstream.

**Bản thử:** [v1.1.2-rc.1](docs/releases/v1.1.2-rc.1.md) sửa chờ OCR gây im
tiếng, con trỏ trắng làm nhiễu và phục hồi capture khi dùng Remote Desktop.
Cài thủ công gói cập nhật app vào bản đã có; **v1.1.1 vẫn là bản stable**.

## Có gì trong v1.1.1?

Bản vá này sửa đọc lặp khi OCR tách/gộp từ, mũi tên hội thoại nhấp nháy hoặc
chữ đang hiện dần; đồng thời xử lý lỗi thiết bị âm thanh báo sai số kênh.
Xem [ghi chú bản vá](docs/releases/v1.1.1.md). Các tính năng hiện có:

- OCR Anh/Nhật offline, phát hiện ngôn ngữ hoặc chọn thủ công.
- Khoanh box hội thoại để chỉ quét phần cần dịch; lưu riêng theo game. Cách
  exclude cũ vẫn có thể dùng lại.
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
| Giảm so với v1.0.0 | **35,4%** | **41,9%** | **67,8%** |
| v1.1.1 — vá OCR lặp/âm thanh² | Chưa đo | Chưa đo | Chưa đo |
| v1.1.2-rc.1 — vá giọng đọc/RDP | Chưa đo | Chưa đo | Chưa đo |

Máy đo: Windows 11, Core i7-13700F, RTX 4090 24 GB. Hai lượt dùng cùng ảnh
hội thoại đã khoanh box, OCR Anh → dịch Việt, HauhauCS Qwen3 4B Q4_K_M local,
ba cặp context cố định và Nano speaker 7, tám bước/hai luồng CPU. Không có
câu trước đang chờ đọc. So sánh gồm cả chuyển đầu ra từ đệm/MME sang
streaming/WASAPI trên cùng thiết bị loa.

¹ Số streaming được đo trong giai đoạn phát triển v1.1.0, trước thay đổi
prompt/updater cuối cùng; chưa đo lại riêng bộ cài đã phát hành. Phép đo chưa
tính thời gian tải model, chờ capture, chữ chạy trong game hoặc chờ đọc hết
câu trước. Đây không phải phép đo FPS game, độ trễ âm thanh qua loa thực hay p95.

² Chạy lại OCR thật trên 10 ảnh GBA đã lưu cho kết quả **10 → 1 yêu cầu đọc**,
với thời gian phát lại được kiểm soát, bản dịch mô phỏng và không phát giọng.
Đây là kiểm chứng chống lặp, không phải TTFA. Chữ đang hiện dần được chờ thích
ứng; chưa đo lại TTFA riêng cho v1.1.1 và không dùng số bản cũ để đại diện.

Xem [phương pháp và cách đo lại](docs/PERFORMANCE.md#v110-streaming-profile)
và [các mẫu thời gian công khai](docs/benchmarks/ttfa.json). Mỗi bản mới sẽ có
dòng riêng khi có số đo tương đương; không dùng lại số bản cũ cho bản chưa đo.

## Cài Windows offline

Trong [Releases](https://github.com/hungdinhxuan/Ludivoxa/releases), tải EXE và
**cả ba file BIN** của cùng phiên bản, đặt chung thư mục rồi chạy EXE. Có
`SHA256SUMS.txt` để kiểm tra. Tổng khoảng 4,9 GB; app cài xong khoảng 6,4 GB.
Python, Ollama, OCR/TTS models và HauhauCS Qwen3 4B Aggressive Q4_K_M đã có sẵn.
Bản build chưa ký số. Các file Source code tự động của GitHub không chứa model.

Nếu đang dùng v1.1.0, vào tab **Updates** để cập nhật v1.1.1. Nếu dùng
v1.0.0 hoặc muốn cập nhật thủ công, tải **Ludivoxa-1.1.1-App-Update.exe**, chạy
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
