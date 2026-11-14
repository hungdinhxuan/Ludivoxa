# Ludivoxa

Ứng dụng OCR game offline, dịch bằng LLM local và tự đọc tiếng Việt.

[English](README.md) · [Hướng dẫn](docs/USER_GUIDE.md) ·
[Changelog](CHANGELOG.md) · [Tải bản phát hành](https://github.com/hungdinhxuan/Ludivoxa/releases)

Ludivoxa được phát triển từ [Interpreter](https://github.com/bquenin/interpreter)
của qube/bquenin, giữ pipeline và giao diện Qt hiện có. Dự án này bắt đầu bằng
**v1.0.0**, độc lập với phiên bản của upstream.

## Có gì trong v1.0.0?

- OCR Anh/Nhật offline, phát hiện ngôn ngữ hoặc chọn thủ công.
- Khoanh box hội thoại để chỉ quét phần cần dịch; lưu riêng theo game. Cách
  exclude cũ vẫn có thể dùng lại.
- Ollama local hoặc OpenAI-compatible/GPU server LAN; giữ context, prompt tùy
  chỉnh, mặc định dịch sang tiếng Việt và không tự fallback sang cloud.
- Overlay, TTS hoặc cả hai; tự đọc mỗi bản dịch ổn định mới.
- Nano có 11 preset, clone từ file ghi âm local nếu bạn cung cấp; đồng thời
  giữ 67 lựa chọn Piper. Không kèm mẫu clone giọng cá nhân.
- Xử lý nền, cache, chống đọc lặp và phát âm thanh đã đệm đủ.
- Đóng app cuối cùng tự tắt Ollama riêng cùng model runners; giữ nguyên server
  local/LAN dùng chung. Mở lại app phải tải model lại.

## Cài Windows offline

Trong [Releases](https://github.com/hungdinhxuan/Ludivoxa/releases), tải EXE và
**cả ba file BIN** của cùng phiên bản, đặt chung thư mục rồi chạy EXE. Có
`SHA256SUMS.txt` để kiểm tra. Tổng khoảng 4,9 GB; app cài xong khoảng 6,4 GB.
Python, Ollama, OCR/TTS models và HauhauCS Qwen3 4B Aggressive Q4_K_M đã có sẵn.
Bản build chưa ký số. Các file Source code tự động của GitHub không chứa model.

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
