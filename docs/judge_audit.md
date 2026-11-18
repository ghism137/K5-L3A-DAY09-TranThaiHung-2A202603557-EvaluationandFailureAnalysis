# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

FAIL là lớp dương (việc của judge là **tìm** lỗi). Judge chạy ở chế độ replay: verdict nằm sẵn trong CSV, không gọi model.

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** cả ba (confusion tp 24 · fp 4 · fn 6 · tn 66) | **dùng** (sau khi sửa rubric, xem dưới) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Trượt** κ và recall (confusion tp 9 · fp 0 · fn 21 · tn 70) | **loại** khỏi gate |

Tỷ lệ FAIL quan sát so với đã hiệu chỉnh (Rogan-Gladen, `corrected = (obs + spec − 1) / (sens + spec − 1)`):
- tone_ok: quan sát 0.28 → hiệu chỉnh **0.30** (nhãn người: 30/100). Judge chỉ lệch nhẹ, hiệu chỉnh gần như không đổi con số.
- helpful: quan sát **0.09** → hiệu chỉnh 0.30. Judge báo 9% lỗi trong khi thật ra 30%: thiếu hai phần ba số lỗi. Phép hiệu chỉnh đưa về 0.30 nhưng dựa trên recall 0.3, nên sai số của ước lượng rất lớn: không dùng một con số như vậy để chặn release.

## 10 case bất đồng của tone_ok (đã audit hết, yêu cầu ≥5)
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | Người | Xưng "em" + "nha" suồng sã với khách. Rubric thêm: *xưng hô chuẩn của LogiX là "LogiX / anh/chị"; "em…nha/nhé" là FAIL*. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ … rồi nha." | PASS | FAIL | Người | Gọi khách là "bạn". Cùng luật xưng hô; liệt kê từ cấm: "bạn", "nha", "nhé". |
| 3 | H-029 "Dạ, vui lòng chờ. Hệ thống đang xử lý, không cần nhắn thêm." | PASS | FAIL | Người | Giọng ra lệnh. Judge chỉ thấy "Dạ, vui lòng". Rubric thêm: *không ra lệnh cho khách ("không cần nhắn thêm")*. |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Ngắn nhưng đủ ý, lịch sự. Judge phạt vì thiếu lời xin lỗi. Rubric: *xin lỗi không bắt buộc với trả lời ngắn đúng; chỉ cần lịch sự*. |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ … ạ." | FAIL | PASS | Người | CS LogiX cho phép dùng "refund". Rubric: *từ vựng trong danh sách cho phép (refund, ship) không bị phạt*. |
| 6 | H-063 "…đã hoàn 20.000đ và sẽ hoàn thêm cho anh/chị ạ." | PASS | FAIL | Người | Hứa "sẽ hoàn thêm" ngoài kết quả hệ thống. Đây là lỗi **sự thật**, không phải giọng điệu: chuyển cho code (kiểm số tiền trong reply, FM-08), tone rubric không gánh. |
| 7 | H-070 "…hoàn 90.000đ … ạ 😂😂" | PASS | FAIL | Người | Emoji không hợp ngữ cảnh khiếu nại. Rubric: *không emoji trong trả lời khiếu nại* (kiểm bằng regex, rẻ hơn judge). |
| 8 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn theo chính sách hiện hành ạ." | FAIL | PASS | **Judge** | Ghi chú CSV: nhãn người sai (đọc nhầm ticket). **Sửa nhãn người**, không sửa judge. Đây là ví dụ vì sao nhãn người cũng phải được kiểm. |
| 9 | H-088 "Dạ, dạ, LogiX đã hoàn 40.000đ …" | FAIL | PASS | Người (một phần) | Lặp "Dạ" là lỗi gõ, không phải giọng điệu. Tone rubric không nên phạt; nếu muốn bắt thì thành tiêu chí "định dạng" riêng. |
| 10 | H-094 "Da, LogiX da hoan 40.000đ cho don O-341, xin loi vi su bat tien." | PASS | FAIL | Người | Dịch máy, mất dấu tiếng Việt. Rubric: *văn bản phải có dấu*; kiểm bằng code (tỉ lệ ký tự có dấu). |

Thống kê: judge sai 8/10, người sai 1/10 (H-081), 1 ca nhập nhằng (H-088). Hướng sai của judge: 6 ca nó **bỏ sót** (PASS khi người FAIL: #1, 2, 3, 6, 7, 10) và 4 ca **phạt oan**. Cả sáu ca bỏ sót là lỗi mà một quy tắc cụ thể trong rubric hoặc một kiểm tra bằng code bắt được; rubric mơ hồ kiểu "thân thiện" là gốc.

**Hành động sau audit:** (a) viết lại rubric tone với 5 luật rõ ở cột "Ghi chú" (xưng hô, từ cấm, không ra lệnh, không emoji, có dấu, lời xin lỗi không bắt buộc); (b) sửa nhãn H-081 từ PASS sang FAIL; (c) đẩy ba luật máy kiểm được (emoji, dấu, số tiền) sang code; (d) đo lại κ/P/R trên một tập nhãn mới trước khi đưa judge vào CI.

## 5 case audit của helpful (lý do loại)
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú |
|---|---|---|---|---|---|
| 1 | HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." | PASS | FAIL (sai tiền) | Người | Ledger ghi 40.000đ. Câu trơn tru, judge không có ledger nên không thể biết. |
| 2 | HL-020 "…hoàn 90.000đ cho đơn O-303 ạ." | PASS | FAIL (sai tiền) | Người | Ledger 120.000đ. |
| 3 | HL-016 (câu dài, hai lần xin lỗi, "hoàn 55.000đ cho đơn O-404") | PASS | FAIL (sai tiền) | Người | Ledger 35.000đ. Câu càng dài, càng lịch sự, judge càng dễ cho qua: thiên vị độ dài/văn phong. |
| 4 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL (sai xưng hô) | Người | Lỗi xưng hô mà tone_ok mới là tiêu chí lo. |
| 5 | HL-100 "…hoàn 55.000đ cho đơn O-112 ạ." | PASS | FAIL (sai tiền) | Người | Ledger 35.000đ. |

## Kết luận
- **Loại `helpful`** khỏi gate: recall 0.30 nghĩa là bỏ sót 21/30 lỗi thật (precision 1.0 không cứu được: judge hiếm khi báo FAIL nhưng khi báo thì đúng). 18/21 ca bỏ sót là "sai tiền", tức `FM-08`; 3/21 là sai xưng hô. Judge **không nhìn thấy ledger**, nên không thể chấm một thứ chỉ ledger mới biết. Việc đó đã có code: `ReplyMatchesLedger` (và `RefundAmountCorrect`). κ = 0.375 < 0.6 chỉ xác nhận điều này.
- **Dùng `tone_ok`** (κ 0.757, P 0.857, R 0.8) nhưng **chỉ như tín hiệu theo dõi cho giọng điệu**, không chặn release về tiền/approval (những thứ đó luôn là code). Chưa nối vào CI vì chưa có API key; muốn đưa vào gate phải có bước đo lại sau khi sửa rubric.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (n = 100, một tập duy nhất); cần nhãn người mới để có held-out thật. Với n = 100, khoảng tin cậy của κ rộng (cỡ ±0.1), nên 0.757 chưa phải một con số chính xác. Nhãn người cũng sai (H-081), nên "người" chỉ là một tham chiếu tốt hơn, không phải chân lý.
