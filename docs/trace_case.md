# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Case chính: C-002@11 trên `seeded_regression`

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run ID `E-seeded_regression-20261118-b1f1` (item E-002) |
| Triệu chứng | Lab 8 grader FAIL `status resolved != denied`. Ledger ghi 20.000đ, nhãn là 0; `refund_count` 1 so với 0. Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." nghe trôi chảy và khớp ledger, nên **chỉ đọc reply thì không thấy lỗi**. |
| Bước sai đầu tiên | **Bước 3**, kind `model_override`, payload `{"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | Bước 1 `handoff` và bước 2 `investigator` đúng: `lookup_order` thấy đơn O-202 (`visible: true`) và trả `delay_days` từ DB. Chỉ ở bước 3 agent ghi đè số ngày của DB bằng số ngày khách tự khai trong ticket ("giao trễ 5 ngày"). Bước 4 quote 20.000đ và bước 5-6 chỉ là hệ quả. |
| Failure mode | **FM-03** ticket_trust (`docs/failure_taxonomy.md`) |
| Đối chứng | `fixed` C-002@11: không có event `model_override`; bước 3 `quote {amount_vnd: 0, reason: "no_entitlement"}`; reply "chưa đủ điều kiện hoàn"; ledger 0, Lab 8 PASS. |
| Regression test | `tests/test_lab9_trace_case.py::test_fm03_ticket_trust_c002_seed11`: replay C-002@11, xác nhận có `model_override`, `DbFactKept` = False và `RefundAmountCorrect` = False trên `seeded_regression`; cả hai = True trên `fixed`. Đã thấy fail trên variant lỗi, pass trên `fixed`. Trong eval set, item **E-002** (`failure_modes: [FM-03]`) giữ vai trò regression ở mức CI: gate chặn `seeded_regression` ở `refund_amount_correct` và `pass_rate`. |
| Chưa chắc chắn | Trace không nói **vì sao** agent chọn tin ticket (MockLLM không có lý do). Công tắc `quiet_override` cùng lỗi nhưng không ghi event `model_override`: khi đó `DbFactKept` mù và chỉ `RefundAmountCorrect` bắt được, nên không được chỉ dựa vào grader mức bước. |

## Case phụ: lỗi mà Lab 8 không thấy (FM-08, C-008@11)

| Trường | Nội dung |
|---|---|
| Trial | `misreport C-008@11` (gài từng công tắc, không có trong `VARIANTS`) |
| Triệu chứng | Lab 8 grader **PASS**. Ledger 20.000đ khớp nhãn (approval giới hạn 20.000). Reply nói "đã hoàn 35.000đ". |
| Bước sai đầu tiên | **Bước 4**, kind `reply`: số tiền trong câu (35.000) khác số host ghi ở ledger (20.000). Bước 3 `quote` 35.000 đúng; chính host mới là bên giới hạn xuống 20.000. |
| Failure mode | **FM-08** misreport |
| Regression test | `tests/test_lab9_trace_case.py::test_fm08_misreport_is_invisible_to_lab8_but_caught_by_ours`: `lab8_pass` = True, `RefundAmountCorrect` = True, `ReplyMatchesLedger` = False. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
