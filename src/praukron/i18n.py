"""Offline dashboard catalogues; authored prose is an opaque interpolation.

Messages use their English copy as stable keys. The HTML boundary localizes
text and accessible names, never identifiers, links, form values or records.
"""

from __future__ import annotations

import html
import copy
import json
import re
from functools import lru_cache
from html.parser import HTMLParser

START, END = '\ue000', '\ue001'


def verbatim(value: object) -> str:
    return START + str(value).replace(START, '').replace(END, '') + END


def unmark(value: str) -> str:
    return value.replace(START, '').replace(END, '')


def protect(data):
    """Copy the compiled display model, marking only authored prose."""
    result = copy.deepcopy(data)

    def mark(record, *fields):
        for key in fields:
            if isinstance(record.get(key), str) and record[key]:
                record[key] = verbatim(record[key])

    mark(result['project'], 'name')
    mark(result['thesis'], 'statement')
    for collection, fields in {
        'tasks': ('title', 'evidence', 'owner'),
        'phases': ('name', 'outcome'), 'modules': ('name', 'outcome'),
        'gates': ('name', 'description'),
        'assumptions': ('title', 'assumption', 'basis', 'permissions', 'appliedIn'),
        'responses': ('text', 'by', 'via'), 'decisions': ('title', 'evidence'),
        'journal': ('title', 'text'), 'milestones': ('text',),
        'debt': ('title', 'debt', 'trigger', 'exitCondition'),
    }.items():
        for record in result.get(collection, []):
            mark(record, *fields)
    for contract in [*result['acceptance'].values(), *result['invariants']]:
        mark(contract, 'title', 'evidence')
        for criterion in contract['criteria']:
            mark(criterion, 'text', 'evidence')
    for event in result['operations']['events']:
        mark(event, 'title', 'error', 'evidence', 'tool', 'target', 'agent', 'artifact')
    for assumption in result['assumptions']:
        recorded = assumption.get('recorded') or ''
        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})(.*)', recorded, re.S)
        if match and match[2]:
            assumption['recorded'] = match[1] + verbatim(match[2])
    for root in result['mission']['roots']:
        mark(root, 'title')
    for group in result['mission']['review'].values():
        for item in group:
            if item['kind'] != 'validation':
                mark(item, 'title')
            elif item['title'].startswith('Owner action: '):
                item['title'] = 'Owner action: ' + verbatim(item['title'][14:])
    return result


# One entry per language: native name, date order, and the complete messages.
_VI = {
    'Home': 'Trang chủ', 'Work': 'Công việc', 'Review': 'Duyệt', 'Audit': 'Kiểm tra',
    'Language': 'Ngôn ngữ', 'Theme': 'Giao diện', 'System': 'Theo hệ thống', 'Light': 'Sáng', 'Dark': 'Tối',
    'Search everything': 'Tìm kiếm tất cả', 'Search records': 'Tìm trong hồ sơ', 'Close': 'Đóng',
    'Task, decision, assumption, or recorded words…': 'Công việc, quyết định, giả định hoặc nội dung đã ghi…',
    'Search by ID, title or recorded words.': 'Tìm theo mã, tiêu đề hoặc nội dung đã ghi.',
    'Nothing matched. Try an ID or fewer words.': 'Không tìm thấy. Hãy thử mã hoặc ít từ hơn.',
    'results. Use arrow keys or Tab to navigate.': 'kết quả. Dùng phím mũi tên hoặc Tab để di chuyển.',
    'result. Use arrow keys or Tab to navigate.': 'kết quả. Dùng phím mũi tên hoặc Tab để di chuyển.',
    'task matches': 'công việc phù hợp',
    'Search decision ID or title…': 'Tìm mã hoặc tiêu đề quyết định…',
    'Search task ID or title…': 'Tìm mã hoặc tiêu đề công việc…',
    'Search decisions': 'Tìm quyết định', 'Search tasks': 'Tìm công việc',
    'Search assumptions': 'Tìm giả định', 'Filter assumptions…': 'Lọc giả định…',
    'Workspaces': 'Không gian làm việc', 'Active filters': 'Bộ lọc đang dùng', 'Filtered by': 'Đang lọc theo',
    'Clear all': 'Xóa bộ lọc', 'Show all': 'Hiện tất cả', 'Remove filter': 'Bỏ bộ lọc',
    'Filters cleared': 'Đã xóa bộ lọc', 'tasks match': 'công việc phù hợp', 'selected': 'đã chọn',
    'No task here matches the active filters.': 'Không có công việc phù hợp với bộ lọc.',
    'item hidden by the active filters.': 'mục bị bộ lọc ẩn.', 'items hidden by the active filters.': 'mục bị bộ lọc ẩn.',
    'Current phase': 'Giai đoạn hiện tại', 'current phase (none)': 'giai đoạn hiện tại (chưa có)',
    'current phase': 'giai đoạn hiện tại', 'Phase timeline': 'Tiến trình các giai đoạn',
    'Current': 'Hiện tại', 'Completed': 'Hoàn thành', 'Upcoming': 'Sắp tới', 'Ready': 'Sẵn sàng',
    'In progress': 'Đang làm', 'Waiting for human': 'Chờ bạn quyết định',
    'Waiting for dependencies': 'Đang chờ phụ thuộc', 'Waiting for deps': 'Chờ phụ thuộc',
    'Blocked by failure': 'Bị chặn do lỗi', 'Blocked': 'Bị chặn',
    'waiting for dependencies': 'đang chờ phụ thuộc', 'waiting for human': 'chờ bạn quyết định',
    'blocked by failure': 'bị chặn do lỗi', 'in progress': 'đang làm',
    'completed': 'hoàn thành', 'upcoming': 'sắp tới', 'ready': 'sẵn sàng',
    'Next human action': 'Việc cần bạn quyết', 'Next phase gate': 'Điều kiện chuyển giai đoạn',
    'Root blocker': 'Nguyên nhân chặn', 'Root blockers': 'Nguyên nhân chặn', 'Work in progress': 'Công việc đang làm',
    'No current phase': 'Chưa có giai đoạn hiện tại', 'No phase is active.': 'Chưa có giai đoạn đang thực hiện.',
    'No phase outcome recorded.': 'Chưa ghi kết quả cần đạt của giai đoạn.',
    'No modules recorded for this phase.': 'Chưa ghi mô-đun cho giai đoạn này.', 'Modules in this phase': 'Mô-đun trong giai đoạn này',
    'No phase exit pending': 'Không có giai đoạn chờ kết thúc', 'Ready to exit': 'Sẵn sàng kết thúc giai đoạn',
    'No active root blocker recorded': 'Chưa ghi nguyên nhân chặn đang có hiệu lực',
    'None. No active agent sessions are recorded.': 'Không có. Chưa ghi phiên tác nhân đang hoạt động.',
    'No direct human decision is actionable now. Continue ready work or inspect upcoming decisions.': 'Hiện chưa có quyết định nào cần bạn xử lý. Hãy tiếp tục công việc sẵn sàng hoặc xem các quyết định sắp tới.',
    'Open Review →': 'Mở mục Duyệt →', 'Owner authority is required for': 'Cần chủ dự án quyết định về',
    'downstream tasks depend on it. Other dependencies may still apply.': 'công việc phía sau phụ thuộc vào việc này. Các phụ thuộc khác vẫn có thể còn.',
    'in Review →': 'trong mục Duyệt →', 'Validate:': 'Xác nhận giả định:',
    'Provisional assumption affecting': 'Giả định tạm thời ảnh hưởng đến',
    'unfinished tasks. A response requires agent reconciliation; it does not itself unblock work.': 'công việc chưa hoàn thành. Tác nhân cần cập nhật theo phản hồi; phản hồi chưa tự gỡ chặn công việc.',
    'Inspect assumption and respond →': 'Xem giả định và phản hồi →',
    'owner actions': 'việc cần chủ dự án xử lý', 'owner action': 'việc cần chủ dự án xử lý',
    'assumptions to validate': 'giả định cần xác nhận', 'assumption to validate': 'giả định cần xác nhận',
    'Execution completion': 'Tiến độ thực hiện', 'Validation strength': 'Mức độ kiểm chứng',
    'Operations activity': 'Hoạt động vận hành', 'Project figures': 'Số liệu dự án',
    'tasks done': 'công việc hoàn thành', 'reviewed or verified': 'đã duyệt hoặc xác minh',
    'recorded events': 'sự kiện đã ghi', 'Inspect work and dependencies →': 'Xem công việc và phụ thuộc →',
    'Latest recorded chronicle date': 'Ngày ghi nhật ký dự án gần nhất', 'Not recorded': 'Chưa ghi',
    'Execution': 'Thực hiện', 'Current work': 'Công việc hiện tại', 'All work': 'Tất cả công việc',
    'Current phase, work in progress and their prerequisites. The full registry and graph below always include every task.': 'Giai đoạn hiện tại, công việc đang làm và các việc cần hoàn thành trước. Danh sách và sơ đồ bên dưới luôn gồm tất cả công việc.',
    'Work scope': 'Phạm vi công việc', 'Work inspection': 'Xem chi tiết công việc',
    'Nothing ready': 'Chưa có việc sẵn sàng', 'waiting for you': 'việc chờ bạn',
    'Execution states, scroll horizontally for more': 'Trạng thái công việc; cuộn ngang để xem thêm',
    'overall': 'tổng cộng', 'more': 'mục nữa', 'No tasks in': 'Không có công việc ở trạng thái',
    'for the selected scope.': 'trong phạm vi đã chọn.', 'Responsible:': 'Người phụ trách:',
    'Authority:': 'Thẩm quyền:', 'unassigned': 'chưa phân công', 'Runtime: unknown': 'Thời gian chạy: chưa rõ',
    'Upcoming: not in current phase': 'Sắp tới: ngoài giai đoạn hiện tại',
    'Direct human action': 'Việc cần người quyết định', 'Upcoming owner decision': 'Quyết định sắp tới của chủ dự án',
    'Direct technical blocker': 'Nguyên nhân chặn kỹ thuật', 'future obligation': 'trách nhiệm trong tương lai',
    'Cause, evidence and affected tasks': 'Nguyên nhân, bằng chứng và công việc bị ảnh hưởng',
    'Cause:': 'Nguyên nhân:', 'Transitive impact:': 'Ảnh hưởng gián tiếp:',
    'Resolving this cause removes this dependency; other roots may still block these tasks.': 'Xử lý nguyên nhân này sẽ gỡ một phụ thuộc; các nguyên nhân khác vẫn có thể chặn những công việc này.',
    'No root blockers recorded. Waiting for ordinary dependencies is not a failure.': 'Chưa ghi nguyên nhân chặn. Chờ công việc phụ thuộc hoàn thành không phải là lỗi.',
    'No unfinished downstream tasks.': 'Không có công việc phía sau chưa hoàn thành.',
    'active root blockers': 'nguyên nhân chặn đang có hiệu lực', 'distinct downstream tasks': 'công việc phía sau không trùng lặp',
    'future owner obligations.': 'trách nhiệm sắp tới của chủ dự án.',
    'Next gate & milestones': 'Điều kiện và mốc tiếp theo', 'Next milestones': 'Mốc tiếp theo',
    'No upcoming milestone recorded.': 'Chưa ghi mốc sắp tới.', 'Critical path': 'Đường găng',
    'Task registry': 'Danh sách công việc', 'Full task registry': 'Danh sách đầy đủ công việc',
    'All tasks': 'Tất cả công việc', 'Phases': 'Giai đoạn', 'Product hierarchy': 'Cấu trúc sản phẩm',
    'Dependency graph': 'Sơ đồ phụ thuộc', 'Complete dependency graph': 'Sơ đồ đầy đủ các phụ thuộc',
    'Task dependencies': 'Phụ thuộc công việc', 'No dependencies': 'Không có phụ thuộc',
    'Dependency graph; dependencies flow left to right. Full links below.': 'Sơ đồ phụ thuộc từ trái sang phải. Các liên kết đầy đủ ở bên dưới.',
    'Breakdown': 'Phân tích', 'Execution breakdown and progress': 'Phân tích và tiến độ thực hiện',
    'Execution progress': 'Tiến độ thực hiện', 'Execution completion by phase': 'Tiến độ theo giai đoạn',
    'Execution status breakdown': 'Phân bố trạng thái công việc', 'Scope': 'Phạm vi',
    'Done / total': 'Hoàn thành / tổng', 'Completion': 'Tiến độ', 'Tasks / total': 'Công việc / tổng',
    'No tasks': 'Không có công việc', 'no tasks': 'không có công việc', 'Zero tasks:': 'Trạng thái không có công việc:',
    'No execution tasks recorded.': 'Chưa ghi công việc thực hiện.', 'No phase': 'Không thuộc giai đoạn',
    'Phase-independent': 'Độc lập với giai đoạn', 'No module': 'Chưa có mô-đun', 'More modules': 'Mô-đun khác',
    'tasks complete': 'công việc hoàn thành', 'Task completion': 'Tiến độ công việc',
    'Criteria passed, started work': 'Tiêu chí đạt của công việc đã bắt đầu',
    'Reviewed or verified tasks': 'Công việc đã duyệt hoặc xác minh', 'Gate readiness': 'Mức sẵn sàng của điều kiện chuyển giai đoạn',
    'of all execution tasks': 'trong tổng công việc thực hiện', 'more in work not started': 'tiêu chí thuộc công việc chưa bắt đầu',
    'not run': 'chưa chạy', 'fail': 'không đạt', 'Clear': 'Không có vấn đề',
    'Task': 'Công việc', 'Tasks': 'Công việc', 'Title': 'Tiêu đề', 'Phase': 'Giai đoạn',
    'Module': 'Mô-đun', 'Domain': 'Lĩnh vực', 'Status': 'Trạng thái', 'State': 'Trạng thái',
    'Validation': 'Kiểm chứng', 'Strength': 'Mức độ', 'Share': 'Tỷ lệ',
    'execution': 'thực hiện', 'operations': 'vận hành', 'Project operations': 'Vận hành dự án',
    'DONE': 'Hoàn thành', 'WIP': 'Đang làm', 'BLOCKED': 'Bị chặn', 'TODO': 'Chưa làm',
    'HUMAN_VERIFIED': 'Người xác minh', 'AI_REVIEWED': 'AI đã duyệt', 'SYNTHETIC': 'Kiểm chứng tổng hợp', 'UNTESTED': 'Chưa duyệt',
    'PASS': 'Đạt', 'FAIL': 'Không đạt', 'NOT_RUN': 'Chưa chạy', 'MISSING': 'Thiếu',
    'COMPLETE': 'Hoàn thành', 'ACTIVE': 'Đang thực hiện', 'EXIT_PENDING': 'Chờ kết thúc',
    'GREEN': 'Đạt', 'RED': 'Chưa đạt', 'YELLOW': 'Cần chú ý', 'UNKNOWN': 'Chưa rõ',
    'OPEN': 'Chưa xử lý', 'CONFIRMED': 'Đã xác nhận', 'REVISED': 'Đã sửa', 'REJECTED': 'Đã bác bỏ', 'WITHDRAWN': 'Đã rút lại',
    'HIGH': 'Cao', 'MEDIUM': 'Trung bình', 'LOW': 'Thấp', 'ACCEPTED': 'Đã chấp nhận',
    'SUPERSEDED': 'Đã thay thế', 'PROPOSED': 'Đề xuất', 'RECONSTRUCTED': 'Tái dựng', 'Reconstructed': 'Tái dựng',
    'Human decision inbox': 'Việc cần bạn quyết', 'Answering live': 'Đang nhận phản hồi',
    'Actionable now': 'Cần xử lý ngay', 'Upcoming decisions': 'Quyết định sắp tới',
    'Assumptions requiring validation': 'Giả định cần xác nhận', 'Previously answered': 'Đã phản hồi',
    'No items.': 'Không có mục nào.', 'Nothing waits for you.': 'Không có việc chờ bạn.',
    'Each card starts at OK. A card you open or mark reviewed is confirmed when you submit. Unopened cards record nothing.': 'Mỗi thẻ mặc định là Đồng ý. Khi gửi, chỉ những thẻ bạn đã mở hoặc đánh dấu đã xem mới được xác nhận. Thẻ chưa mở không ghi phản hồi.',
    'This page is read-only. To answer, run': 'Trang này chỉ để xem. Để phản hồi, chạy',
    'Owner decisions are separate from agent work. Assumptions are provisional, never approval.': 'Quyết định của chủ dự án được tách khỏi công việc của tác nhân. Giả định chỉ là tạm thời, chưa phải phê duyệt.',
    'Filter assumptions and review order': 'Lọc giả định và thứ tự duyệt', 'No assumptions to filter.': 'Không có giả định để lọc.',
    'Ordered by immediate impact, recorded permission or HIGH-impact sensitivity, then downstream cost. Permission or HIGH-impact sensitivity is the recorded proxy for irreversible consequences.': 'Sắp xếp theo ảnh hưởng trước mắt, quyền truy cập hoặc mức ảnh hưởng Cao đã ghi, rồi đến chi phí của công việc phía sau. Quyền truy cập và mức ảnh hưởng Cao giúp nhận diện hậu quả khó đảo ngược.',
    'Impact': 'Ảnh hưởng', 'Kind': 'Loại', 'permission': 'quyền truy cập', 'Permission': 'Quyền truy cập',
    'Assumed:': 'Giả định:', 'Why:': 'Cơ sở:', 'Applied in:': 'Áp dụng tại:', 'If revised:': 'Nếu sửa lại:',
    'Already rests on it:': 'Đã dựa vào giả định:', 'The agent assumed an access rule:': 'Tác nhân đã giả định quy tắc truy cập:',
    'the agent reconciles': 'tác nhân cập nhật', 'and names what changed in': 'và ghi rõ thay đổi trong',
    'Reconciled by': 'Reconciled by', 'Responses': 'Phản hồi', 'No response yet.': 'Chưa có phản hồi.',
    'Answered; waiting for the agent to reconcile it.': 'Đã phản hồi; đang chờ tác nhân cập nhật.',
    'To respond, run': 'Để phản hồi, chạy', 'and open the link it prints. This page is read-only.': 'và mở liên kết được in ra. Trang này chỉ để xem.',
    'awaiting the agent': 'chờ tác nhân', 'affected unfinished tasks:': 'công việc chưa hoàn thành bị ảnh hưởng:',
    'Owner response awaits agent reconciliation.': 'Phản hồi của chủ dự án đang chờ tác nhân cập nhật.',
    'Access rule needs owner validation.': 'Quy tắc truy cập cần chủ dự án xác nhận.',
    'Provisional choice needs owner validation.': 'Lựa chọn tạm thời cần chủ dự án xác nhận.',
    'Previously answered assumption; retained for reference.': 'Giả định đã được phản hồi, giữ lại để tham khảo.',
    'Inspect reconciliation': 'Xem cập nhật theo phản hồi', 'Review response options': 'Xem lựa chọn phản hồi',
    'Inspect record': 'Xem hồ sơ', 'No task linked': 'Chưa liên kết công việc',
    'Affects:': 'Ảnh hưởng:', 'This is an access rule the agent assumed:': 'Đây là quy tắc truy cập tác nhân đã giả định:',
    'OK confirms it as the rule.': 'Đồng ý sẽ xác nhận quy tắc này.', 'OK': 'Đồng ý',
    'Mark reviewed': 'Đánh dấu đã xem', 'Feedback': 'Góp ý', 'Revise': 'Sửa lại', 'Reject': 'Bác bỏ', 'Guide': 'Hướng dẫn',
    'Your words, recorded verbatim': 'Nội dung của bạn sẽ được ghi nguyên văn', 'Action for': 'Hành động cho', 'Feedback on': 'Góp ý về',
    'Guidance on': 'Hướng dẫn cho', 'Guidance for the agent on': 'Hướng dẫn tác nhân về',
    'recorded verbatim': 'ghi nguyên văn', 'Guide: run': 'Để hướng dẫn, chạy', 'to give the agent guidance on': 'để hướng dẫn tác nhân về',
    'No owner guidance recorded.': 'Chưa ghi hướng dẫn của chủ dự án.',
    'Future obligation. Finish dependencies and reach the relevant phase first.': 'Trách nhiệm trong tương lai. Cần hoàn thành phụ thuộc và tới đúng giai đoạn trước.',
    'Affected work:': 'Công việc bị ảnh hưởng:', 'Owner authority': 'Thẩm quyền chủ dự án',
    'Review acceptance and decisions:': 'Xem tiêu chí nghiệm thu và quyết định:',
    'The contract is met; owner verification is required.': 'Đã đáp ứng hợp đồng; cần chủ dự án xác minh.',
    "The contract is not yet met; the owner's decision is needed.": 'Chưa đáp ứng hợp đồng; cần chủ dự án quyết định.',
    'Owner decision awaits dependencies or its phase.': 'Quyết định của chủ dự án đang chờ phụ thuộc hoặc đúng giai đoạn.',
    'Owner verification awaits agent reconciliation.': 'Xác minh của chủ dự án đang chờ tác nhân cập nhật.',
    'Inspect prerequisites': 'Xem điều kiện tiên quyết', 'Run owner command': 'Chạy lệnh của chủ dự án',
    'Affected record:': 'Hồ sơ bị ảnh hưởng:', 'Resolve': 'Xử lý', 'Verify': 'Xác minh',
    'Verified by': 'Được xác minh bởi', 'waiting for the agent to record HUMAN_VERIFIED.': 'chờ tác nhân ghi nhận Người xác minh.',
    'I verified': 'Tôi đã xác minh', 'What you verified on': 'Nội dung bạn đã xác minh về',
    'What you checked, recorded verbatim': 'Nội dung bạn đã kiểm tra, ghi nguyên văn',
    'Verify: run': 'Để xác minh, chạy', 'or': 'hoặc', 'at a terminal.': 'tại cửa sổ dòng lệnh.',
    'Recording as GitHub user': 'Ghi phản hồi bằng tài khoản GitHub', 'No GitHub login: run': 'Chưa đăng nhập GitHub: chạy',
    'then reload.': 'rồi tải lại.', 'Submit review': 'Gửi phản hồi', 'Answered, not yet submitted': 'Đã trả lời, chưa gửi',
    'Needs work outside this page': 'Cần xử lý bên ngoài trang này', 'Nothing here takes an answer': 'Không có mục cần phản hồi tại đây',
    'still needs a response': 'mục vẫn cần phản hồi', 'still need a response': 'mục vẫn cần phản hồi',
    'needs work outside this page': 'mục cần xử lý bên ngoài trang này', 'need work outside this page': 'mục cần xử lý bên ngoài trang này',
    'answered. Submit to record them.': 'mục đã trả lời. Gửi để ghi nhận.',
    'Nothing to record: open a card to confirm it, or write feedback.': 'Chưa có phản hồi để ghi. Hãy mở thẻ để xác nhận hoặc viết góp ý.',
    'write your feedback, or choose OK': 'hãy viết góp ý hoặc chọn Đồng ý', 'Recorded': 'Đã ghi', 'Reloading…': 'Đang tải lại…',
    'Evidence and governance': 'Bằng chứng và quản trị', 'Recorded evidence, decisions and history. No events are inferred.': 'Bằng chứng, quyết định và lịch sử đã ghi. Không suy diễn thêm sự kiện.',
    'Gates': 'Điều kiện chuyển giai đoạn', 'Technical debt': 'Nợ kỹ thuật', 'Decisions': 'Quyết định',
    'Architecture decisions': 'Quyết định kiến trúc', 'Filter decisions': 'Lọc quyết định',
    'Acceptance criteria and evidence': 'Tiêu chí nghiệm thu và bằng chứng', 'No acceptance criteria recorded.': 'Chưa ghi tiêu chí nghiệm thu.',
    'Task records and implementation evidence': 'Hồ sơ công việc và bằng chứng triển khai',
    'Execution diagnostics': 'Chẩn đoán thực hiện', 'Obstacles': 'Trở ngại', 'Owner guidance': 'Hướng dẫn của chủ dự án',
    'Responses and reconciliation history': 'Phản hồi và lịch sử cập nhật', 'Journal history': 'Lịch sử nhật ký',
    'No responses recorded.': 'Chưa ghi phản hồi.', 'No journal entries recorded.': 'Chưa có mục nhật ký.',
    'No decisions recorded.': 'Chưa ghi quyết định.', 'Supersedes': 'Thay thế', 'Superseded by': 'Được thay thế bởi',
    'Affects': 'Ảnh hưởng', 'Inferred from': 'Suy ra từ', 'nothing cited': 'chưa dẫn nguồn',
    'Task details': 'Chi tiết công việc', 'Dependencies': 'Phụ thuộc', 'Blocks': 'Chặn',
    'Acceptance': 'Nghiệm thu', 'Full evidence': 'Bằng chứng đầy đủ', 'Open task record in Audit': 'Mở hồ sơ công việc trong mục Kiểm tra',
    'Inherited invariants': 'Bất biến kế thừa', 'External blockers · project operations': 'Nguyên nhân chặn bên ngoài · vận hành dự án',
    'Evidence': 'Bằng chứng', 'Operational events': 'Sự kiện vận hành', 'Schedule': 'Lịch trình', 'Source': 'Nguồn',
    'No criteria recorded.': 'Chưa ghi tiêu chí.', 'Nothing downstream': 'Không có công việc phía sau',
    'None recorded': 'Chưa ghi', 'none recorded': 'chưa ghi', 'Unscheduled': 'Chưa lên lịch',
    'Claimed:': 'Nhận việc:', 'Implementation files:': 'Tệp triển khai:', 'Implementation symbols:': 'Ký hiệu triển khai:',
    'No schedule recorded': 'Chưa ghi lịch trình', 'Assumptions': 'Giả định', 'Contract evidence:': 'Bằng chứng hợp đồng:',
    'No evidence recorded': 'Chưa ghi bằng chứng', 'Source record missing': 'Thiếu hồ sơ nguồn',
    'Execution tasks only. Validation strength records review, not testing: UNTESTED means no review was recorded, even when criteria hold TEST or RUNTIME evidence. Completion is separate.': 'Chỉ tính công việc thực hiện. Mức độ kiểm chứng phản ánh việc duyệt: Chưa duyệt nghĩa là chưa ghi nhận đánh giá, kể cả khi tiêu chí có bằng chứng TEST hoặc RUNTIME. Tiến độ hoàn thành được tính riêng.',
    'Operational trace': 'Dấu vết vận hành', 'Summary': 'Tổng quan', 'Active operations': 'Vận hành đang thực hiện',
    'Failures and warnings': 'Lỗi và cảnh báo', 'Retries': 'Lần thử lại', 'Timeline': 'Dòng thời gian', 'Filter timeline': 'Lọc dòng thời gian',
    'Operations maintains and inspects the environment execution happens in. It never counts as project progress, and it stays reviewable because it can explain why execution is blocked, failing, or wrong.': 'Vận hành duy trì và kiểm tra môi trường thực hiện. Hoạt động này không tính vào tiến độ dự án; dấu vết được giữ lại để giải thích nguyên nhân chặn, lỗi hoặc sai lệch.',
    'No retries recorded.': 'Chưa ghi lần thử lại.', 'No events recorded.': 'Chưa ghi sự kiện.',
    'No phases recorded.': 'Chưa ghi giai đoạn.', 'Nothing here.': 'Không có mục nào.',
    'No open work on the critical path.': 'Không có công việc chưa hoàn thành trên đường găng.',
    'No open work to order.': 'Không có công việc chưa hoàn thành cần sắp thứ tự.',
    'Critical path · dependency order': 'Đường găng · thứ tự phụ thuộc', 'Phase flow · top to bottom': 'Luồng giai đoạn · từ trên xuống',
    'Gates → phase exits': 'Điều kiện → kết thúc giai đoạn', 'Dependency ordering · steps, not dates': 'Thứ tự phụ thuộc · theo bước',
    'Calendar Gantt · recorded schedules': 'Biểu đồ Gantt · lịch đã ghi', 'scroll to explore': 'cuộn để xem thêm',
    'Step': 'Bước', 'Views': 'Chế độ xem', 'Diagram views': 'Chế độ xem sơ đồ',
    'No task carries schedule metadata.': 'Chưa có công việc ghi lịch trình.', 'open tasks are unscheduled.': 'công việc chưa hoàn thành chưa có lịch.',
    'No drawable dates. Check the recorded start, end or duration in the Mermaid source.': 'Không có ngày hợp lệ để vẽ. Kiểm tra ngày bắt đầu, kết thúc hoặc thời lượng trong nguồn Mermaid.',
    'Bars use recorded starts and ends or durations. Unscheduled tasks have no calendar position. Exact metadata appears on every row.': 'Thanh biểu đồ dùng ngày bắt đầu, kết thúc hoặc thời lượng đã ghi. Công việc chưa có lịch không được đặt lên trục thời gian. Mỗi hàng hiển thị dữ liệu gốc.',
    'Invalid or unsupported schedule; no bar drawn': 'Lịch không hợp lệ hoặc chưa hỗ trợ; không vẽ thanh',
    'unscheduled tasks not shown. No dates or durations are inferred.': 'công việc chưa có lịch không được hiển thị. Không suy diễn ngày hoặc thời lượng.',
    'unscheduled task not shown. No dates or durations are inferred.': 'công việc chưa có lịch không được hiển thị. Không suy diễn ngày hoặc thời lượng.',
    'duration': 'thời lượng', 'Diagram source': 'Nguồn sơ đồ', 'Mermaid source': 'Nguồn Mermaid',
    'project state': 'trạng thái dự án', 'execution tasks done': 'công việc thực hiện hoàn thành', 'compiled from': 'tổng hợp từ',
    'Generated by': 'Được tạo bằng', 'This page is derived and read-only: it reports authority and cannot change it. Edit': 'Trang này được tổng hợp và chỉ để xem. Để thay đổi nội dung, hãy sửa',
    'and compile again.': 'và tổng hợp lại.',
    'Exit authority for': 'Thẩm quyền kết thúc', 'execution tasks open': 'công việc thực hiện chưa hoàn thành',
    'exit criteria unmet': 'tiêu chí kết thúc chưa đạt', 'holds up': 'đang chặn', 'trace': 'dấu vết',
    'phase exit awaits gate evidence': 'kết thúc giai đoạn đang chờ bằng chứng đạt điều kiện',
    'exit authority': 'thẩm quyền kết thúc', 'exit waits on': 'kết thúc đang chờ',
    'owner': 'chủ dự án', 'agent': 'tác nhân', 'current': 'hiện tại', 'active': 'đang hoạt động',
    'downstream tasks': 'công việc phía sau', 'downstream': 'công việc phía sau',
    'tasks': 'công việc', 'task': 'công việc', 'modules': 'mô-đun', 'module': 'mô-đun',
    'assumptions': 'giả định', 'assumption': 'giả định', 'decisions': 'quyết định', 'decision': 'quyết định',
    'events': 'sự kiện', 'event': 'sự kiện', 'done': 'hoàn thành', 'of': 'trên',
    'All': 'Tất cả', 'all': 'tất cả', 'None': 'Không có', 'none': 'không có',
    'not recorded': 'chưa ghi', 'not stated': 'chưa nêu', 'reconstructed': 'tái dựng',
}
_VI.update({
    'Tool calls': 'Lượt gọi công cụ', 'Mini-actions': 'Thao tác nhỏ', 'Changes and mutations': 'Thay đổi dữ liệu',
    'No tool calls recorded.': 'Chưa ghi lượt gọi công cụ.', 'No mini-actions recorded.': 'Chưa ghi thao tác nhỏ.',
    'No changes and mutations recorded.': 'Chưa ghi thay đổi dữ liệu.', 'No failed events recorded.': 'Chưa ghi sự kiện lỗi.',
    'No open operations tasks.': 'Không có công việc vận hành chưa hoàn thành.',
    'No open technical debt recorded.': 'Chưa ghi nợ kỹ thuật còn tồn tại.',
    'No operational events are recorded. Tool calls, mini-actions, failures, retries and mutations appear here once agents append them to': 'Chưa ghi sự kiện vận hành. Lượt gọi công cụ, thao tác nhỏ, lỗi, lần thử lại và thay đổi dữ liệu sẽ hiện ở đây khi tác nhân ghi vào',
    'nothing is reconstructed from the journal.': 'không tái dựng sự kiện từ nhật ký.',
    'No tasks yet.': 'Chưa có công việc.', 'Product thesis': 'Luận đề sản phẩm', 'Detail:': 'Chi tiết:',
    'No product thesis authored in THESIS.md.': 'Chưa viết luận đề sản phẩm trong THESIS.md.',
    'Legacy tasks with no module': 'Công việc cũ chưa có mô-đun', 'Operations holding up execution': 'Vận hành đang chặn thực hiện',
    'To unblock:': 'Để gỡ chặn:', 'Who acts:': 'Người xử lý:', 'Gate evidence →': 'Bằng chứng đạt điều kiện →',
    'Calendar Gantt': 'Biểu đồ Gantt', 'Dependency ordering': 'Thứ tự phụ thuộc', 'graph': 'sơ đồ',
    'Select a bar or legend entry to filter every chart, table, and the Needs you panel; select it again to remove it. Bars split by status: done, in progress, blocked, to do.': 'Chọn thanh hoặc chú giải để lọc các biểu đồ, bảng và mục Việc cần bạn quyết; chọn lại để bỏ lọc. Thanh được chia theo trạng thái: hoàn thành, đang làm, bị chặn, chưa làm.',
    'task carries real schedule metadata;': 'công việc có lịch trình đã ghi;', 'tasks carry real schedule metadata;': 'công việc có lịch trình đã ghi;',
    'is unscheduled. Dependency ordering is shown separately from calendar dates, and no duration is inferred.': 'công việc chưa có lịch. Thứ tự phụ thuộc được hiển thị riêng với ngày lịch; không suy diễn thời lượng.',
    'are unscheduled. Dependency ordering is shown separately from calendar dates, and no duration is inferred.': 'công việc chưa có lịch. Thứ tự phụ thuộc được hiển thị riêng với ngày lịch; không suy diễn thời lượng.',
    'open operations tasks.': 'công việc vận hành chưa hoàn thành.', 'recorded': 'đã ghi', 'via': 'qua', 'on': 'về',
    'DEPENDENCY_BLOCKER': 'Chờ phụ thuộc', 'GATE_BLOCKER': 'Chưa đạt điều kiện', 'PHASE_BLOCKER': 'Chờ giai đoạn',
    'HUMAN_BLOCKER': 'Chờ người quyết định', 'EXTERNAL_BLOCKER': 'Chặn từ bên ngoài', 'FAILURE': 'Lỗi',
    'VALIDATION_GAP': 'Thiếu kiểm chứng', 'EVIDENCE_GAP': 'Thiếu bằng chứng',
    'TEST': 'Kiểm thử', 'RUNTIME': 'Chạy thực tế', 'INSPECTION': 'Kiểm tra',
    'Evidence recorded, review missing': 'Đã ghi bằng chứng, chưa duyệt',
    'No exercised evidence, no review': 'Chưa có bằng chứng thực thi, chưa duyệt',
    'No open execution work.': 'Không có công việc thực hiện chưa hoàn thành.',
    'Execution failures': 'Lỗi thực hiện', 'failed criteria': 'tiêu chí không đạt',
    'related operational evidence:': 'bằng chứng vận hành liên quan:', 'no operational evidence recorded': 'chưa ghi bằng chứng vận hành',
    'external blocker': 'nguyên nhân chặn bên ngoài', 'No gates recorded.': 'Chưa ghi điều kiện chuyển giai đoạn.',
    'open verifier': 'công việc xác minh chưa hoàn thành', 'Resolved or invalidated': 'Đã xử lý hoặc không còn hiệu lực',
    'introduced by': 'phát sinh từ', 'repaid by': 'được xử lý bởi', 'trigger:': 'Điều kiện kích hoạt:', 'exit:': 'Điều kiện kết thúc:',
    'trigger not reached': 'chưa đến điều kiện kích hoạt', 'trigger approaching': 'sắp đến điều kiện kích hoạt', 'trigger reached': 'đã đến điều kiện kích hoạt',
    'RESOLVED': 'Đã xử lý', 'INVALIDATED': 'Không còn hiệu lực', 'SCHEDULED': 'Đã lên lịch',
    'cannot exit while': 'chưa thể kết thúc khi', 'is red': 'chưa đạt', 'has': 'có',
    'unfinished execution tasks': 'công việc thực hiện chưa hoàn thành', 'waits on': 'đang chờ',
    'finish': 'hoàn thành', 'open tasks': 'công việc chưa hoàn thành', 'record the evidence that turns': 'ghi bằng chứng để xác nhận',
    'green': 'đạt', 'blocks': 'chặn', 'exit': 'kết thúc', 'Gate': 'Điều kiện',
    'task': 'công việc', 'in': 'trong', 'tool': 'công cụ', 'target': 'đối tượng',
    'artifact': 'sản phẩm', 'retry of': 'thử lại từ', 'under': 'thuộc', 'affects': 'ảnh hưởng', 'evidence': 'bằng chứng',
    'unresolved': 'chưa xử lý', 'success': 'thành công', 'failure': 'thất bại', 'warning': 'cảnh báo',
    'unknown': 'chưa rõ', 'tool-call': 'gọi công cụ', 'command': 'lệnh', 'action': 'thao tác', 'mutation': 'thay đổi dữ liệu',
    'CONFIRM': 'Xác nhận', 'REVISE': 'Sửa lại', 'REJECT': 'Bác bỏ', 'GUIDE': 'Hướng dẫn', 'VERIFY': 'Xác minh',
    'Decide': 'Quyết định', 'Assumption': 'Giả định', 'Response': 'Phản hồi', 'Journal': 'Nhật ký', 'Criterion': 'Tiêu chí',
    'unfinished': 'chưa hoàn thành', 'Show': 'Hiện thêm', 'details': 'chi tiết', 'from': 'từ',
    'No review recorded for this task.': 'Chưa ghi nhận đánh giá công việc này.',
    'cannot complete while': 'chưa thể hoàn thành khi', 'criteria are unmet': 'tiêu chí chưa đạt',
    'record passing evidence for': 'ghi bằng chứng đạt cho', 'the owner finishes': 'chủ dự án hoàn thành',
    'requires a human owner verification.': 'cần chủ dự án trực tiếp xác minh.', 'claimed': 'nhận việc',
    'Owner action:': 'Việc chủ dự án cần xử lý:', 'validation': 'kiểm chứng',
    'needs a VERIFY from a named owner': 'cần phản hồi VERIFY từ chủ dự án đã được khai báo',
    'requires a human (owner) verification.': 'cần chủ dự án trực tiếp xác minh.',
    'No owner declaration for this task.': 'Chưa khai báo chủ dự án cho công việc này.',
    'No schedule for this task.': 'Chưa có lịch cho công việc này.',
    'PLANNED': 'Dự kiến', 'MANUAL': 'Thủ công', 'ACCEPTANCE_BLOCKER': 'Chưa đạt nghiệm thu',
    'Needs your review': 'Cần bạn duyệt', 'Needs you': 'Việc cần bạn quyết',
    'Waiting for the agent': 'Đang chờ tác nhân', 'Nothing waits for the agent.': 'Không có việc chờ tác nhân.',
    'Nothing waits for your review.': 'Không có việc chờ bạn duyệt.', 'Recently changed': 'Thay đổi gần đây',
    'Settled': 'Đã xử lý', 'Nothing settled yet.': 'Chưa có mục đã xử lý.',
    'An assumption holds no authority; the records it is applied in do.': 'Giả định không có thẩm quyền; thẩm quyền thuộc về hồ sơ áp dụng giả định.',
    'Access rules an agent assumed. Review these first.': 'Quy tắc truy cập tác nhân đã giả định. Hãy duyệt những mục này trước.',
    'HIGH first, then those resting on finished or critical-path work, then the current phase.': 'Ưu tiên mức Cao, rồi đến giả định của công việc đã xong hoặc trên đường găng, sau đó là giai đoạn hiện tại.',
    'open, in the': 'mục chưa xử lý, trong', 'panel beside every tab.': 'bên cạnh mỗi thẻ.',
    'Only an owner named in': 'Chỉ chủ dự án có tên trong', 'can verify these. An agent never does.': 'mới được xác minh. Tác nhân không được làm thay.',
    'No assumptions recorded. Agents record a provisional choice in': 'Chưa ghi giả định. Tác nhân ghi lựa chọn tạm thời vào',
    'before relying on it.': 'trước khi dựa vào lựa chọn đó.',
    'Work ahead of phase closure': 'Công việc đi trước việc kết thúc giai đoạn',
    'Search assumptions…': 'Tìm giả định…', 'Permissions': 'Quyền truy cập', 'Permission assumptions': 'Giả định về quyền truy cập',
    'Open assumptions': 'Giả định chưa xử lý', 'Responded, not yet reconciled': 'Đã phản hồi, chưa cập nhật',
    'Confirmed, revised, rejected, or withdrawn': 'Đã xác nhận, sửa, bác bỏ hoặc rút lại',
    'owner action:': 'việc chủ dự án cần xử lý:',
    'from the project root, run': 'từ thư mục gốc dự án, chạy', 'interactively (Windows:': 'ở chế độ tương tác (Windows:',
    "The active gh login must be named in an ADR's Owners field; establish that owner declaration first if missing. Only the owner may do this, never an agent.": 'Tài khoản gh đang đăng nhập phải có trong trường Owners của một ADR. Nếu chưa có, hãy khai báo chủ dự án trước. Chỉ chủ dự án được thực hiện, tác nhân không được làm thay.',
    "The active gh login must be named in an ACCEPTED ADR's Owners field; establish that owner declaration first if missing. Only the owner may do this, never an agent.": 'Tài khoản gh đang đăng nhập phải có trong trường Owners của một ADR đã chấp nhận. Nếu chưa có, hãy khai báo chủ dự án trước. Chỉ chủ dự án được thực hiện, tác nhân không được làm thay.',
    'Praukron: link needed': 'Praukron: cần liên kết đầy đủ', 'Open the full link': 'Mở liên kết đầy đủ',
    'This review page needs the link printed by': 'Trang duyệt cần liên kết được in ra bởi',
    'including its': 'bao gồm phần', 'part. The token keeps other pages on this machine from writing your answers.': 'trong liên kết. Mã này ngăn trang khác trên máy ghi phản hồi của bạn.',
    'refused': 'Yêu cầu bị từ chối', 'too large': 'Nội dung quá lớn',
    'No gates or phase exits recorded.': 'Chưa ghi điều kiện hoặc việc kết thúc giai đoạn.',
    'nothing recorded.': 'chưa ghi nội dung.',
    'Owner authority requires a human decision or verification.': 'Thẩm quyền của chủ dự án cần người trực tiếp quyết định hoặc xác minh.',
    'No events recorded for this task.': 'Chưa ghi sự kiện cho công việc này.',
    'declared': 'đã khai báo', 'phase': 'giai đoạn', 'exit-authority': 'thẩm quyền kết thúc', 'gate': 'điều kiện chuyển giai đoạn',
    'nothing to record': 'không có phản hồi để ghi', 'nothing was recorded:': 'chưa ghi phản hồi:',
    'another write is in progress; try again': 'đang có lượt ghi khác; hãy thử lại',
    'an interrupted archive was rolled back; try again': 'đã khôi phục thao tác lưu trữ bị gián đoạn; hãy thử lại',
    'is not a YYYY-MM-DD date': 'không phải ngày có dạng YYYY-MM-DD',
    'could not ask gh who is logged in:': 'không kiểm tra được tài khoản gh đang đăng nhập:',
    'is not one of': 'không thuộc các lựa chọn', 'the response to': 'phản hồi cho', 'has no text': 'chưa có nội dung',
    'VERIFY applies only to an Authority: owner task, not': 'VERIFY chỉ áp dụng cho công việc có Authority: owner, không áp dụng cho',
    'VERIFY is recorded only interactively: from': 'Chỉ ghi VERIFY qua thao tác trực tiếp từ',
    'at a terminal, never by an agent': 'tại cửa sổ dòng lệnh; tác nhân không được làm thay',
    'is not an assumption in ASSUMPTIONS.md': 'không phải giả định trong ASSUMPTIONS.md',
    'is neither an assumption nor a task': 'không phải giả định hoặc công việc',
    'is not a task in TASKS.md': 'không phải công việc trong TASKS.md',
    'applies to assumptions; a task takes GUIDE': 'áp dụng cho giả định; công việc nhận phản hồi GUIDE',
    'the GitHub CLI is not installed: a response records who gave it as the active': 'chưa cài GitHub CLI; phản hồi ghi nhận người gửi bằng tài khoản đang đăng nhập qua',
    'login (install gh and run': '(hãy cài gh và chạy',
    'no GitHub login: run': 'chưa đăng nhập GitHub: chạy',
    'a response records who gave it as the active': 'phản hồi ghi nhận người gửi bằng tài khoản đang đăng nhập qua',
    'login': 'đăng nhập', 'Failed to fetch': 'Không kết nối được. Hãy mở lại liên kết từ máy chủ đang chạy.',
})
CATALOGUES = {
    'en': {'name': 'English', 'dateOrder': 'ymd', 'messages': {key: key for key in _VI}},
    'vi': {'name': 'Tiếng Việt', 'dateOrder': 'dmy', 'messages': _VI},
}


def language(code: str | None) -> str:
    return code if code in CATALOGUES else 'en'


@lru_cache(maxsize=1)
def message_pattern():
    keys = sorted(CATALOGUES['en']['messages'], key=len, reverse=True)
    return re.compile(r'(?<![\w-])(?:' + '|'.join(re.escape(k).replace(r'\ ', r'\s+') for k in keys) + r')(?![\w-])')


def translate(source: str, lang: str) -> str:
    """Translate UI fragments while treating marked records as opaque."""
    parts = re.split('(' + START + '.*?' + END + r'|\{\w+\}|`[^`]*`)', source, flags=re.S)
    messages = CATALOGUES[language(lang)]['messages']
    pattern = message_pattern()
    for i, part in enumerate(parts):
        if part.startswith((START, '`')) or re.fullmatch(r'\{\w+\}', part):
            parts[i] = unmark(part)
            continue
        if pattern:
            parts[i] = pattern.sub(lambda m: messages[' '.join(m.group().split())] if lang != 'en' else m.group(), part)
        if CATALOGUES[language(lang)]['dateOrder'] == 'dmy':
            parts[i] = re.sub(r'\b(\d{4})-(\d{2})-(\d{2})\b', r'\3/\2/\1', parts[i])
    return ''.join(parts)


class Localizer(HTMLParser):
    """Preserve markup and register text locations for the offline switcher."""

    def __init__(self, lang):
        super().__init__(convert_charrefs=False)
        self.lang = language(lang)
        self.output = []
        self.stack = []
        self.sources = []
        self.attributes = []
        self.pending = ''

    def flush(self):
        if not self.pending:
            return
        raw, self.pending = self.pending, ''
        source = html.unescape(raw)
        excluded = any(tag in {'script', 'style', 'code', 'pre'} for tag in self.stack)
        if excluded:
            self.output.append(unmark(raw))
            return
        if source.strip():
            self.sources.append(source)
        self.output.append(unmark(raw) if self.lang == 'en' else html.escape(translate(source, self.lang), quote=False))

    def handle_starttag(self, tag, attrs):
        self.flush()
        original = self.get_starttag_text()
        translated_attrs = {}
        # Keep source bytes except for localized attributes and prose markers.
        for name, value in attrs:
            if value is None:
                continue
            if name in {'aria-label', 'title', 'placeholder', 'data-agg-text'}:
                translated_attrs[name] = value
                translated = translate(value, self.lang)
                original = re.sub(r'(' + re.escape(name) + r'\s*=\s*)([\"\'])(.*?)\2',
                                  lambda m: m[1] + m[2] + html.escape(translated, quote=True) + m[2], original, count=1)
        if translated_attrs:
            self.attributes.append(translated_attrs)
        self.output.append(unmark(original))
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        self.flush()
        self.output.append('</' + tag + '>')
        if tag in self.stack:
            while self.stack:
                popped = self.stack.pop()
                if popped == tag:
                    break

    def handle_data(self, data):
        self.pending += data

    def handle_entityref(self, name):
        self.pending += '&' + name + ';'

    def handle_charref(self, name):
        self.pending += '&#' + name + ';'

    def handle_comment(self, data):
        self.flush()
        self.output.append('<!--' + data + '-->')

    def handle_decl(self, decl):
        self.flush()
        self.output.append('<!' + decl + '>')


def localize(page: str, lang: str, recorded: str | None = None) -> str:
    parser = Localizer(lang)
    parser.feed(page)
    parser.flush()
    data = {'catalogues': CATALOGUES, 'texts': parser.sources, 'attributes': parser.attributes,
            'recorded': recorded, 'rendered': language(lang)}
    island = json.dumps(data, ensure_ascii=True).replace('</', '<\\/')
    runtime = '<script type="application/json" id="i18n-data">' + island + '</script><script>' + SCRIPT + '</script>'
    return ''.join(parser.output).replace('<!--i18n-runtime-->', runtime)


def style() -> str:
    messages = {'empty': 'No task here matches the active filters.',
                'answered': 'Answered, not yet submitted', 'offpage': 'Needs work outside this page'}
    return '\n'.join(':root[lang="' + code + '"]{' + ''.join(
        '--ui-' + name + ':' + json.dumps(catalog['messages'][key], ensure_ascii=False) + ';'
        for name, key in messages.items()) + '}' for code, catalog in CATALOGUES.items())


SCRIPT = r"""
const I18N = JSON.parse(document.getElementById('i18n-data').textContent);
const languageControl = document.getElementById('dashboard-language');
let activeLanguage = I18N.rendered;
const textOrigins = new WeakMap(), attributeOrigins = new WeakMap();
const protectedText = value => '\ue000' + String(value == null ? '' : value).replace(/[\ue000\ue001]/g, '') + '\ue001';
const unmarkText = value => value.replace(/[\ue000\ue001]/g, '');
const escapePattern = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const messageKeys = Object.keys(I18N.catalogues.en.messages).sort((a, b) => b.length - a.length);
const messagePattern = new RegExp('(?<![\\p{L}\\p{N}_-])(?:' + messageKeys.map(key => escapePattern(key).replace(/ /g, '\\s+')).join('|') + ')(?![\\p{L}\\p{N}_-])', 'gu');
function translateUI(source, lang = activeLanguage) {
  const catalog = I18N.catalogues[lang] || I18N.catalogues.en;
  return String(source).split(/(\ue000[\s\S]*?\ue001|\{\w+\}|`[^`]*`)/).map(part => {
    if (part.startsWith('\ue000') || part.startsWith('`') || /^\{\w+\}$/.test(part)) return unmarkText(part);
    let text = part.replace(messagePattern, key => lang === 'en' ? key : catalog.messages[key.replace(/\s+/g, ' ')]);
    if (catalog.dateOrder === 'dmy') text = text.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, '$3/$2/$1');
    return text;
  }).join('');
}
function uiNodes(root = document.body) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.textContent.trim() && !node.parentElement.closest('script,style,code,pre')) nodes.push(node);
  }
  return nodes;
}
// Include the document title; whitespace is deliberately not part of the map.
const initialNodes = uiNodes(document.documentElement);
initialNodes.forEach((node, index) => textOrigins.set(node, {source: I18N.texts[index], rendered: node.textContent}));
function uiSource(element) {
  return [...element.childNodes].map(node => {
    const entry = textOrigins.get(node);
    return entry && entry.rendered === node.textContent ? entry.source : node.textContent;
  }).join('');
}
function uiAttribute(element, name) {
  const entry = (attributeOrigins.get(element) || {})[name];
  return entry ? entry.source : element.getAttribute(name);
}
const attributeSelector = '[aria-label],[title],[placeholder],[data-agg-text]';
document.querySelectorAll(attributeSelector).forEach((node, index) => {
  const originals = I18N.attributes[index] || {};
  attributeOrigins.set(node, Object.fromEntries(Object.entries(originals).map(([name, source]) => [name, {source, rendered: node.getAttribute(name)}])));
});
function paintLanguage() {
  observer.disconnect();
  uiNodes(document.documentElement).forEach(node => {
    let entry = textOrigins.get(node);
    if (!entry || entry.rendered !== node.textContent) entry = {source: node.textContent};
    if (entry.source === undefined) entry.source = node.textContent;
    entry.rendered = translateUI(entry.source);
    if (node.textContent !== entry.rendered) node.textContent = entry.rendered;
    textOrigins.set(node, entry);
  });
  document.querySelectorAll(attributeSelector).forEach(node => {
    const entries = attributeOrigins.get(node) || {};
    ['aria-label', 'title', 'placeholder', 'data-agg-text'].forEach(name => {
      if (!node.hasAttribute(name)) return;
      let entry = entries[name];
      if (!entry || entry.rendered !== node.getAttribute(name)) entry = {source: node.getAttribute(name)};
      entry.rendered = translateUI(entry.source);
      if (node.getAttribute(name) !== entry.rendered) node.setAttribute(name, entry.rendered);
      entries[name] = entry;
    });
    attributeOrigins.set(node, entries);
  });
  document.documentElement.lang = activeLanguage;
  languageControl.value = activeLanguage;
  observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ['aria-label', 'title', 'placeholder', 'data-agg-text']});
}
const observer = new MutationObserver(paintLanguage);
function chooseLanguage(stored, recorded, browser) {
  if (I18N.catalogues[stored]) return stored;
  if (recorded) return I18N.catalogues[recorded] ? recorded : 'en';
  const primary = (browser || '').toLowerCase().split('-')[0];
  return I18N.catalogues[primary] ? primary : 'en';
}
let savedLanguage = null;
try { savedLanguage = localStorage.getItem('praukron-language'); } catch (error) {}
activeLanguage = chooseLanguage(savedLanguage, I18N.recorded, navigator.language);
languageControl.addEventListener('change', () => {
  activeLanguage = languageControl.value;
  try { localStorage.setItem('praukron-language', activeLanguage); } catch (error) {}
  paintLanguage();
});
paintLanguage();
"""
