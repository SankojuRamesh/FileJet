from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import inch

WIDTH, HEIGHT = landscape((16 * inch, 9 * inch))

NAVY = colors.HexColor('#081120')
BLUE = colors.HexColor('#2F80ED')
CYAN = colors.HexColor('#4CC9F0')
PURPLE = colors.HexColor('#7C3AED')
GREEN = colors.HexColor('#21C998')
ORANGE = colors.HexColor('#FFB703')
PINK = colors.HexColor('#FF6B9A')
WHITE = colors.Color(1, 1, 1)
SOFT = colors.HexColor('#D9E3F0')
MUTED = colors.HexColor('#8DA0B6')
CARD = colors.HexColor('#101B2E')
ACCENT = colors.HexColor('#1F8DFF')


def page_bg(c):
    c.setFillColor(NAVY)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    c.setFillColor(colors.HexColor('#0E1A2C'))
    c.rect(0, 0, WIDTH, HEIGHT * 0.18, fill=1, stroke=0)
    c.setFillColor(colors.HexColor('#111F35'))
    c.roundRect(0.5 * inch, 0.5 * inch, WIDTH - inch, HEIGHT - inch, 18, fill=1, stroke=0)
    for cx, cy, r, col in [
        (WIDTH * 0.82, HEIGHT * 0.8, 180, colors.HexColor('#18325C')),
        (WIDTH * 0.78, HEIGHT * 0.18, 140, colors.HexColor('#1A3154')),
        (WIDTH * 0.18, HEIGHT * 0.72, 100, colors.HexColor('#142B4A')),
    ]:
        c.setFillColor(col)
        c.circle(cx, cy, r, fill=1, stroke=0)


def add_footer(c, page_num, total_pages=8):
    c.setFillColor(MUTED)
    c.setFont('Helvetica', 9)
    c.drawString(0.9 * inch, 0.6 * inch, 'FileJet')
    c.drawRightString(WIDTH - 0.8 * inch, 0.6 * inch, f'{page_num}/{total_pages}')


def draw_brand(c, x, y):
    c.setFillColor(BLUE)
    c.roundRect(x, y, 0.9 * inch, 0.36 * inch, 8, stroke=0, fill=1)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(x + 0.15 * inch, y + 0.08 * inch, 'FJ')
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 16)
    c.drawString(x + 1.15 * inch, y + 0.04 * inch, 'FileJet')


def draw_card(c, x, y, w, h, title, accent_color, body_lines=None, title_size=16):
    c.setFillColor(CARD)
    c.roundRect(x, y, w, h, 16, fill=1, stroke=0)
    c.setFillColor(accent_color)
    c.roundRect(x, y + h - 4, w, 4, 2, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', title_size)
    c.drawString(x + 0.25 * inch, y + h - 0.42 * inch, title)
    if body_lines:
        c.setFillColor(SOFT)
        c.setFont('Helvetica', 11)
        for idx, line in enumerate(body_lines):
            c.drawString(x + 0.25 * inch, y + h - 0.78 * inch - idx * 0.22 * inch, line)


def add_slide_title(c, title, subtitle=''):
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 30)
    c.drawString(0.9 * inch, HEIGHT - 1.0 * inch, title)
    if subtitle:
        c.setFillColor(MUTED)
        c.setFont('Helvetica', 14)
        c.drawString(0.9 * inch, HEIGHT - 1.45 * inch, subtitle)


def draw_folder_icon(c, x, y, scale=1.0, fill_color=BLUE):
    c.setFillColor(fill_color)
    c.roundRect(x, y, 1.2 * inch * scale, 0.9 * inch * scale, 12, fill=1, stroke=0)
    c.setFillColor(colors.HexColor('#DDEBFF'))
    c.roundRect(x + 0.14 * inch * scale, y + 0.28 * inch * scale, 0.9 * inch * scale, 0.7 * inch * scale, 10, fill=1, stroke=0)
    c.setFillColor(fill_color)
    path = c.beginPath()
    path.moveTo(x + 0.05 * inch * scale, y + 0.52 * inch * scale)
    path.lineTo(x + 0.5 * inch * scale, y + 0.82 * inch * scale)
    path.lineTo(x + 1.1 * inch * scale, y + 0.82 * inch * scale)
    path.lineTo(x + 1.24 * inch * scale, y + 0.52 * inch * scale)
    path.close()
    c.drawPath(path, stroke=0, fill=1)


def draw_network_diagram(c):
    nodes = [
        (2.6 * inch, 5.8 * inch, 'Owner'),
        (8.6 * inch, 5.8 * inch, 'Cloud'),
        (13.2 * inch, 5.8 * inch, 'Recipient'),
    ]
    for x, y, label in nodes:
        c.setFillColor(colors.HexColor('#12233e'))
        c.roundRect(x - 0.9 * inch, y - 0.45 * inch, 1.8 * inch, 0.9 * inch, 18, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 13)
        c.drawCentredString(x, y + 0.08 * inch, label)
    c.setStrokeColor(colors.HexColor('#5FD6FF'))
    c.setLineWidth(2.2)
    c.line(3.5 * inch, 5.8 * inch, 7.7 * inch, 5.8 * inch)
    c.line(9.5 * inch, 5.8 * inch, 12.3 * inch, 5.8 * inch)
    c.setFillColor(colors.HexColor('#7EF2CC'))
    c.circle(5.6 * inch, 5.8 * inch, 0.12 * inch, fill=1, stroke=0)
    c.circle(11.0 * inch, 5.8 * inch, 0.12 * inch, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica', 12)
    c.drawString(4.1 * inch, 5.0 * inch, 'Metadata & permissions')
    c.drawString(9.5 * inch, 5.0 * inch, 'P2P file transfer')


def draw_avatars(c, x, y, colors_list):
    for idx, col in enumerate(colors_list):
        c.setFillColor(col)
        c.circle(x + idx * 0.7 * inch, y, 0.28 * inch, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 10)
        initials = ['A', 'M', 'S', 'J']
        c.drawCentredString(x + idx * 0.7 * inch, y - 0.06 * inch, initials[idx])


def draw_use_case_card(c, x, y, w, h, title, text, accent, icon='✓'):
    c.setFillColor(CARD)
    c.roundRect(x, y, w, h, 18, fill=1, stroke=0)
    c.setFillColor(accent)
    c.circle(x + 0.35 * inch, y + h - 0.42 * inch, 0.18 * inch, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 15)
    c.drawString(x + 0.6 * inch, y + h - 0.52 * inch, title)
    c.setFillColor(SOFT)
    c.setFont('Helvetica', 11)
    lines = text.split(' | ')
    for idx, line in enumerate(lines):
        c.drawString(x + 0.28 * inch, y + h - 0.9 * inch - idx * 0.22 * inch, line)


def draw_compare_table(c):
    rows = [
        ('File transfer', 'Direct P2P, TLS 1.3', 'Cloud relay', 'Cloud relay', 'Direct but server-based'),
        ('Privacy', 'Cloud sees metadata only', 'Files stored in cloud', 'Files stored in cloud', 'Server handles storage'),
        ('Offline send', 'Queue in sender outbox', 'Usually sync only', 'Often limited', 'Manual retry'),
        ('Large files', 'Chunked, resume, SHA-256', 'OK, but costly', 'Good for links', 'Works, but complex'),
        ('Folder sharing', 'Permissions + expiry + groups', 'Shared links', 'Links / folders', 'Access control'),
        ('Best fit', 'Creative teams / internal ops', 'General storage', 'One-off large files', 'Legacy IT teams'),
    ]
    x0, y0, w, h = 0.95 * inch, 2.2 * inch, WIDTH - 1.9 * inch, 4.0 * inch
    col_w = [1.8 * inch, 2.2 * inch, 2.0 * inch, 2.0 * inch, 2.1 * inch]
    c.setFillColor(CARD)
    c.roundRect(x0, y0, w, h, 18, fill=1, stroke=0)
    # header
    c.setFillColor(colors.HexColor('#18335D'))
    c.roundRect(x0, y0 + h - 0.6 * inch, w, 0.6 * inch, 18, fill=1, stroke=0)
    headers = ['FileJet', 'Dropbox / Drive', 'WeTransfer', 'FTP / SFTP']
    label_y = y0 + h - 0.34 * inch
    lx = x0 + 0.25 * inch
    for idx, header in enumerate(headers):
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 12)
        c.drawString(lx, label_y, header)
        lx += col_w[idx + 1]
    # rows
    row_y = y0 + h - 0.9 * inch
    for i, (label, col1, col2, col3, col4) in enumerate(rows):
        if i % 2 == 0:
            c.setFillColor(colors.HexColor('#0F1C2F'))
            c.rect(x0, row_y - i * 0.55 * inch, w, 0.52 * inch, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(x0 + 0.18 * inch, row_y - i * 0.55 * inch + 0.15 * inch, label)
        c.setFont('Helvetica', 9)
        text_x = x0 + 1.85 * inch
        for val in [col1, col2, col3, col4]:
            c.drawString(text_x, row_y - i * 0.55 * inch + 0.15 * inch, val[:20])
            text_x += col_w[1]


def draw_hero_shapes(c):
    # big folder illustration
    x, y = 2.0 * inch, 3.1 * inch
    draw_folder_icon(c, x, y, scale=1.8, fill_color=BLUE)
    # File cards
    c.setFillColor(CARD)
    c.roundRect(x + 2.4 * inch, y + 0.75 * inch, 1.75 * inch, 1.1 * inch, 12, fill=1, stroke=0)
    c.roundRect(x + 2.9 * inch, y + 0.25 * inch, 1.85 * inch, 1.1 * inch, 12, fill=1, stroke=0)
    c.roundRect(x + 3.45 * inch, y + 1.2 * inch, 1.9 * inch, 1.1 * inch, 12, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 13)
    c.drawString(x + 2.55 * inch, y + 1.65 * inch, 'Movie Project')
    c.drawString(x + 3.03 * inch, y + 1.15 * inch, 'Client_Files.zip')
    c.drawString(x + 3.6 * inch, y + 2.1 * inch, 'Logo_Rev2.ai')
    c.setFillColor(GREEN)
    c.roundRect(x + 3.55 * inch, y + 0.35 * inch, 0.9 * inch, 0.2 * inch, 10, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(x + 3.65 * inch, y + 0.38 * inch, 'Ready')


def title_slide(c):
    page_bg(c)
    draw_brand(c, 0.9 * inch, HEIGHT - 0.75 * inch)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 40)
    c.drawString(0.9 * inch, HEIGHT - 2.0 * inch, 'FileJet')
    c.setFillColor(CYAN)
    c.setFont('Helvetica-Bold', 24)
    c.drawString(0.9 * inch, HEIGHT - 2.7 * inch, 'Private file sharing without cloud storage')
    c.setFillColor(SOFT)
    c.setFont('Helvetica', 17)
    c.drawString(0.9 * inch, HEIGHT - 3.5 * inch, 'Share folders, send large files, and control permissions')
    c.drawString(0.9 * inch, HEIGHT - 3.9 * inch, 'directly between computers—without exposing file contents to the cloud.')
    c.setFillColor(CARD)
    c.roundRect(0.9 * inch, 1.3 * inch, 5.0 * inch, 1.15 * inch, 20, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(1.2 * inch, 2.05 * inch, 'Why teams choose FileJet')
    c.setFillColor(SOFT)
    c.setFont('Helvetica', 13)
    c.drawString(1.2 * inch, 1.7 * inch, '• P2P transfer with end-to-end encryption')
    c.drawString(1.2 * inch, 1.42 * inch, '• Resume, retry, and manage permissions')
    c.setFillColor(CARD)
    c.roundRect(6.7 * inch, 1.35 * inch, 2.4 * inch, 1.7 * inch, 18, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 14)
    c.drawString(7.15 * inch, 2.65 * inch, 'Fast')
    c.drawString(7.1 * inch, 2.2 * inch, '8 MiB chunks')
    c.setFillColor(CARD)
    c.roundRect(9.5 * inch, 1.35 * inch, 2.5 * inch, 1.7 * inch, 18, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.drawString(10.0 * inch, 2.65 * inch, 'Secure')
    c.drawString(9.95 * inch, 2.2 * inch, 'TLS 1.3 + SHA-256')
    c.setFillColor(CARD)
    c.roundRect(12.5 * inch, 1.35 * inch, 2.2 * inch, 1.7 * inch, 18, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.drawString(12.9 * inch, 2.65 * inch, 'Control')
    c.drawString(12.85 * inch, 2.2 * inch, 'Roles + expiry')
    draw_hero_shapes(c)
    add_footer(c, 1, 8)


def second_slide(c):
    page_bg(c)
    add_slide_title(c, 'The real problem teams face', 'Traditional file sharing is slow, risky, and hard to control.')
    cards = [
        ('1. File size limits', ['Large media and design files break', 'email and basic transfer tools'], BLUE),
        ('2. Weak control', ['Shared links become public risk', 'without clear roles or expiry'], ORANGE),
        ('3. Cloud exposure', ['A lot of tools store files in the cloud', 'or relay every transfer through a server'], PINK),
        ('4. Work interruptions', ['Downloads stop at the wrong moment,', 'and resumes feel unreliable'], GREEN),
    ]
    x_positions = [1.0 * inch, 4.25 * inch, 7.5 * inch, 10.75 * inch]
    for i, (title, lines, color) in enumerate(cards):
        draw_card(c, x_positions[i], 2.1 * inch, 2.8 * inch, 2.7 * inch, title, color, lines, title_size=15)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 24)
    c.drawString(1.0 * inch, 1.1 * inch, 'Result: teams spend more time waiting, re-sending, and securing files than actual work.')
    add_footer(c, 2, 8)


def third_slide(c):
    page_bg(c)
    add_slide_title(c, 'How FileJet works', 'Cloud keeps only metadata; file data moves directly between computers.')
    draw_network_diagram(c)
    extra = [
        ('Cloud checks identities', 'Users are added by ID, with permissions, groups, and access rules. Only metadata is kept in the cloud.'),
        ('Sender uploads to outbox', 'If the owner is offline, the file waits safely on the sender machine and resumes when online.'),
        ('Owner receives verified chunks', 'Each segment is validated with SHA-256 before being written to disk.'),
    ]
    x_positions = [1.1 * inch, 5.3 * inch, 9.5 * inch]
    widths = [3.6 * inch, 3.6 * inch, 3.6 * inch]
    for i, (title, text) in enumerate(extra):
        c.setFillColor(CARD)
        c.roundRect(x_positions[i], 1.3 * inch, widths[i], 1.5 * inch, 18, fill=1, stroke=0)
        c.setFillColor(CYAN)
        c.roundRect(x_positions[i], 1.3 * inch + 1.12 * inch, widths[i], 0.06 * inch, 0, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 13)
        c.drawString(x_positions[i] + 0.2 * inch, 1.3 * inch + 1.22 * inch, title)
        c.setFillColor(SOFT)
        c.setFont('Helvetica', 11)
        text_lines = text.split(' ')
        wrapped = []
        current = ''
        for word in text_lines:
            if len(current + ' ' + word) <= 28:
                current = current + ' ' + word if current else word
            else:
                wrapped.append(current)
                current = word
        if current:
            wrapped.append(current)
        for idx, line in enumerate(wrapped[:4]):
            c.drawString(x_positions[i] + 0.18 * inch, 1.3 * inch + 0.82 * inch - idx * 0.22 * inch, line)
    add_footer(c, 3, 8)


def fourth_slide(c):
    page_bg(c)
    add_slide_title(c, 'Who uses FileJet?', 'Built for teams that move large files, sensitive assets, and review cycles every day.')
    cards = [
        ('Creative & media', 'Studio managers share folders with editors. They upload dailies, revisions, and client assets without exposing data to a public cloud.', BLUE),
        ('Agency delivery', 'Project teams send campaign files, approvals, version packs, and final assets to clients or internal departments with role-based access.', PURPLE),
        ('Legal & finance', 'Confidential document sets move between counsel, reviewers, and internal teams with permission controls and expiry dates.', GREEN),
        ('Field operations', 'Teams distribute drawings, inspection files, and field reports to remote crews while keeping files on the right local machine.', ORANGE),
    ]
    positions = [(0.9 * inch, 2.2 * inch), (8.2 * inch, 2.2 * inch), (0.9 * inch, 5.3 * inch), (8.2 * inch, 5.3 * inch)]
    sizes = [(6.7 * inch, 1.8 * inch), (6.7 * inch, 1.8 * inch), (6.7 * inch, 1.8 * inch), (6.7 * inch, 1.8 * inch)]
    for i, (title, text, accent) in enumerate(cards):
        x, y = positions[i]
        w, h = sizes[i]
        c.setFillColor(CARD)
        c.roundRect(x, y, w, h, 18, fill=1, stroke=0)
        c.setFillColor(accent)
        c.roundRect(x, y + h - 0.08 * inch, w, 0.07 * inch, 0, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 17)
        c.drawString(x + 0.2 * inch, y + h - 0.45 * inch, title)
        c.setFillColor(SOFT)
        c.setFont('Helvetica', 11)
        wrapped = [text[i:i + 75] for i in range(0, len(text), 75)]
        for idx, line in enumerate(wrapped[:4]):
            c.drawString(x + 0.2 * inch, y + h - 0.8 * inch - idx * 0.22 * inch, line)
    add_footer(c, 4, 8)


def fifth_slide(c):
    page_bg(c)
    add_slide_title(c, 'Core product features', 'Everything teams expect from enterprise file sharing and more.')
    features = [
        ('Folder sharing', 'Share any folder on a PC or NAS with users or groups.', BLUE),
        ('Role permissions', 'Viewer, Uploader, Editor, Manager, or custom access.', GREEN),
        ('Big file transfer', 'Any size, chunked delivery, resume support, and SHA-256 checks.', PURPLE),
        ('Offline queue', 'Files sit in the sender outbox and auto-deliver once online.', ORANGE),
        ('Submitted folders', 'Use drop-box style uploaders that cannot browse folder contents.', PINK),
        ('Upload forms', 'Project and notes metadata stays on the owner machine only.', CYAN),
    ]
    positions = [(0.9 * inch, 3.0 * inch), (5.1 * inch, 3.0 * inch), (9.3 * inch, 3.0 * inch),
                 (0.9 * inch, 1.2 * inch), (5.1 * inch, 1.2 * inch), (9.3 * inch, 1.2 * inch)]
    sizes = [(3.5 * inch, 1.6 * inch), (3.5 * inch, 1.6 * inch), (3.5 * inch, 1.6 * inch),
             (3.5 * inch, 1.6 * inch), (3.5 * inch, 1.6 * inch), (3.5 * inch, 1.6 * inch)]
    for i, (title, desc, color) in enumerate(features):
        x, y = positions[i]
        w, h = sizes[i]
        c.setFillColor(CARD)
        c.roundRect(x, y, w, h, 18, fill=1, stroke=0)
        c.setFillColor(color)
        c.roundRect(x, y + h - 0.06 * inch, w, 0.06 * inch, 0, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 15)
        c.drawString(x + 0.18 * inch, y + h - 0.4 * inch, title)
        c.setFillColor(SOFT)
        c.setFont('Helvetica', 10.5)
        wrapped = [desc[i:i + 32] for i in range(0, len(desc), 32)]
        for idx, line in enumerate(wrapped[:3]):
            c.drawString(x + 0.18 * inch, y + h - 0.72 * inch - idx * 0.18 * inch, line)
    add_footer(c, 5, 8)


def sixth_slide(c):
    page_bg(c)
    add_slide_title(c, 'FileJet vs. other apps', 'Not just another file sync tool—FileJet is built for secure, controlled delivery.')
    draw_compare_table(c)
    add_footer(c, 6, 8)


def seventh_slide(c):
    page_bg(c)
    add_slide_title(c, 'Business value', 'Built to reduce friction, protect data, and keep teams productive.')
    metrics = [
        ('Faster delivery', 'No waiting for cloud uploads or manual re-send loops.', BLUE),
        ('Lower risk', 'Cloud stores metadata only; file contents stay protected.', GREEN),
        ('Clear ownership', 'Folder permissions, expiry, and role models are easy to manage.', PURPLE),
        ('Better scaling', 'Works for large media, internal ops, and remote teams without storage bottlenecks.', ORANGE),
    ]
    left = 1.0 * inch
    for i, (title, desc, color) in enumerate(metrics):
        y = HEIGHT - 2.2 * inch - i * 1.35 * inch
        c.setFillColor(CARD)
        c.roundRect(left, y, 12.3 * inch, 0.92 * inch, 18, fill=1, stroke=0)
        c.setFillColor(color)
        c.circle(left + 0.28 * inch, y + 0.44 * inch, 0.18 * inch, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 17)
        c.drawString(left + 0.55 * inch, y + 0.28 * inch, title)
        c.setFillColor(SOFT)
        c.setFont('Helvetica', 12)
        c.drawString(left + 0.55 * inch, y + 0.04 * inch, desc)
    c.setFillColor(CARD)
    c.roundRect(13.8 * inch, 1.8 * inch, 1.5 * inch, 2.9 * inch, 18, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 22)
    c.drawCentredString(14.55 * inch, 4.1 * inch, 'More')
    c.setFont('Helvetica-Bold', 20)
    c.drawCentredString(14.55 * inch, 3.4 * inch, 'control')
    c.setFont('Helvetica-Bold', 22)
    c.drawCentredString(14.55 * inch, 2.8 * inch, 'Less')
    c.setFont('Helvetica-Bold', 20)
    c.drawCentredString(14.55 * inch, 2.2 * inch, 'risk')
    add_footer(c, 7, 8)


def eighth_slide(c):
    page_bg(c)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 44)
    c.drawString(1.0 * inch, HEIGHT - 1.8 * inch, 'Ready to modernize folder sharing?')
    c.setFillColor(SOFT)
    c.setFont('Helvetica', 20)
    c.drawString(1.0 * inch, HEIGHT - 2.7 * inch, 'FileJet helps teams move big files securely, keep control, and avoid cloud lock-in.')
    c.setFillColor(CARD)
    c.roundRect(1.0 * inch, 2.3 * inch, 5.4 * inch, 2.7 * inch, 20, fill=1, stroke=0)
    c.setFillColor(CYAN)
    c.roundRect(1.0 * inch, 2.3 * inch, 5.4 * inch, 0.08 * inch, 0, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(1.3 * inch, 4.55 * inch, 'Why it matters')
    c.setFillColor(SOFT)
    c.setFont('Helvetica', 13)
    c.drawString(1.3 * inch, 4.15 * inch, '• Secure folder sharing with clear permissions')
    c.drawString(1.3 * inch, 3.8 * inch, '• Large file transfers with pause/resume and integrity checks')
    c.drawString(1.3 * inch, 3.45 * inch, '• Direct delivery between computers, not public storage relay')
    c.drawString(1.3 * inch, 3.1 * inch, '• Works across creative, operations, legal, and field teams')
    c.setFillColor(CARD)
    c.roundRect(7.8 * inch, 2.3 * inch, 6.7 * inch, 2.7 * inch, 20, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont('Helvetica-Bold', 18)
    c.drawString(8.2 * inch, 4.55 * inch, 'Best-fit positioning')
    c.setFillColor(SOFT)
    c.setFont('Helvetica', 13)
    c.drawString(8.2 * inch, 4.1 * inch, 'FileJet is ideal for teams who need:')
    c.drawString(8.2 * inch, 3.75 * inch, '• secure transfer of large assets')
    c.drawString(8.2 * inch, 3.4 * inch, '• controlled internal or external sharing')
    c.drawString(8.2 * inch, 3.05 * inch, '• predictable delivery without cloud data exposure')
    c.drawString(8.2 * inch, 2.7 * inch, '• a better alternative to generic cloud sync tools')
    add_footer(c, 8, 8)


def build_pdf(path):
    c = canvas.Canvas(path, pagesize=landscape((16 * inch, 9 * inch)))
    c.setTitle('FileJet Marketing Deck')
    c.setAuthor('FileJet')
    slides = [title_slide, second_slide, third_slide, fourth_slide, fifth_slide, sixth_slide, seventh_slide, eighth_slide]
    for idx, slide in enumerate(slides, start=1):
        slide(c)
        if idx < len(slides):
            c.showPage()
    c.save()


def build_pptx(path):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    def add_bg(slide):
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(8, 17, 32)
        for x, y, w, h, color in [
            (9.8, 0.2, 3.0, 5.0, RGBColor(24, 45, 76)),
            (10.8, 5.1, 2.2, 2.2, RGBColor(18, 34, 56)),
            (0.6, 0.5, 2.2, 1.8, RGBColor(26, 41, 72)),
            (1.1, 5.6, 2.0, 2.0, RGBColor(30, 52, 86)),
        ]:
            shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
            shape.fill.solid()
            shape.fill.fore_color.rgb = color
            shape.line.fill.background()

    def add_title(slide, title, subtitle=None):
        tb = slide.shapes.add_textbox(Inches(0.7), Inches(0.45), Inches(8.5), Inches(0.6))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        p.text = title
        p.runs[0].font.size = Pt(28)
        p.runs[0].font.bold = True
        p.runs[0].font.color.rgb = RGBColor(255, 255, 255)
        if subtitle:
            tb2 = slide.shapes.add_textbox(Inches(0.75), Inches(0.95), Inches(10), Inches(0.5))
            p2 = tb2.text_frame.paragraphs[0]
            p2.text = subtitle
            p2.runs[0].font.size = Pt(13)
            p2.runs[0].font.color.rgb = RGBColor(170, 188, 213)

    def add_card(slide, x, y, w, h, title, lines, accent):
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(16, 27, 46)
        shape.line.fill.background()
        top = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y + h - 0.08), Inches(w), Inches(0.08))
        top.fill.solid(); top.fill.fore_color.rgb = accent; top.line.fill.background()
        tb = slide.shapes.add_textbox(Inches(x + 0.18), Inches(y + h - 0.56), Inches(w - 0.25), Inches(0.35))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        p.text = title
        p.alignment = PP_ALIGN.LEFT
        p.runs[0].font.bold = True
        p.runs[0].font.size = Pt(17)
        p.runs[0].font.color.rgb = RGBColor(255, 255, 255)
        for idx, line in enumerate(lines):
            tb2 = slide.shapes.add_textbox(Inches(x + 0.18), Inches(y + h - 0.9 - idx * 0.26), Inches(w - 0.25), Inches(0.25))
            p2 = tb2.text_frame.paragraphs[0]
            p2.text = line
            p2.runs[0].font.size = Pt(10)
            p2.runs[0].font.color.rgb = RGBColor(217, 227, 240)

    for slide_index, slide_data in enumerate([
        ('FileJet', 'Private file sharing without cloud storage', ['Why teams choose FileJet', '• P2P transfer with end-to-end encryption', '• Huge file support with resume and integrity checks', '• Clear roles and expiry windows']),
        ('The real problem teams face', 'Traditional file sharing is slow, risky, and hard to control.', ['File size limits', 'Weak control', 'Cloud exposure', 'Work interruptions']),
        ('How FileJet works', 'Cloud keeps metadata; file data moves directly between computers.', ['Cloud checks identities', 'Sender uploads to outbox', 'Owner receives verified chunks']),
        ('Who uses FileJet?', 'Built for teams that move large files, sensitive assets, and review cycles every day.', ['Creative & media', 'Agency delivery', 'Legal & finance', 'Field operations']),
        ('Core product features', 'Everything teams expect from enterprise file sharing and more.', ['Folder sharing', 'Role permissions', 'Big file transfer', 'Offline queue', 'Submitted folders', 'Upload forms']),
        ('FileJet vs. other apps', 'Not just another file sync tool—FileJet is built for secure, controlled delivery.', ['Direct P2P', 'Metadata-only cloud', 'Roles + expiry', 'Resume and verify chunks']),
        ('Business value', 'Built to reduce friction, protect data, and keep teams productive.', ['Faster delivery', 'Lower risk', 'Clear ownership', 'Better scaling']),
        ('Ready to modernize folder sharing?', 'FileJet helps teams move big files securely, keep control, and avoid cloud lock-in.', ['Secure transfer of large assets', 'Controlled internal or external sharing', 'Predictable delivery without cloud data exposure'])
    ], start=1):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_bg(slide)
        if slide_index == 1:
            title_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.1), Inches(5.5), Inches(0.8))
            p = title_box.text_frame.paragraphs[0]
            p.text = 'FileJet'
            p.runs[0].font.size = Pt(28)
            p.runs[0].font.bold = True
            p.runs[0].font.color.rgb = RGBColor(255, 255, 255)
            sub = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(7.5), Inches(0.6))
            p2 = sub.text_frame.paragraphs[0]
            p2.text = 'Private file sharing without cloud storage'
            p2.runs[0].font.size = Pt(20)
            p2.runs[0].font.color.rgb = RGBColor(88, 214, 255)
            cards = [
                (7.0, 1.4, 2.1, 1.7, 'Fast', ['8 MiB chunks'], RGBColor(45, 126, 255)),
                (9.5, 1.4, 2.1, 1.7, 'Secure', ['TLS 1.3 + SHA-256'], RGBColor(60, 180, 120)),
                (11.8, 1.4, 1.5, 1.7, 'Control', ['Roles + expiry'], RGBColor(124, 58, 237)),
            ]
            for x, y, w, h, title, lines, accent in cards:
                add_card(slide, x, y, w, h, title, lines, accent)
            folder = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(2.2), Inches(2.5), Inches(2.6), Inches(2.3))
            folder.fill.solid(); folder.fill.fore_color.rgb = RGBColor(57, 136, 255); folder.line.fill.background()
            inner = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(2.45), Inches(3.1), Inches(2.0), Inches(1.2))
            inner.fill.solid(); inner.fill.fore_color.rgb = RGBColor(214, 232, 255); inner.line.fill.background()
            # Decorative circles
            for x, y, radius, color in [
                (7.5, 5.0, 0.8, RGBColor(77, 150, 255)),
                (8.7, 5.4, 0.5, RGBColor(102, 207, 232)),
                (7.3, 4.0, 0.55, RGBColor(45, 216, 167)),
            ]:
                circ = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(radius), Inches(radius))
                circ.fill.solid(); circ.fill.fore_color.rgb = color; circ.line.fill.background()
        else:
            add_title(slide, slide_data[0], slide_data[1])
            if slide_index in (2,3,4,5,6,7,8):
                positions = [(0.8, 1.9, 2.8, 2.3), (3.9, 1.9, 2.8, 2.3), (7.0, 1.9, 2.8, 2.3), (10.1, 1.9, 2.8, 2.3)]
                accent_colors = [RGBColor(82, 143, 255), RGBColor(92, 217, 165), RGBColor(124, 58, 237), RGBColor(255, 170, 64)]
                for idx, (x, y, w, h) in enumerate(positions[:min(len(slide_data[2]), 4)]):
                    add_card(slide, x, y, w, h, slide_data[2][idx], ['Data', 'secure', 'and clear'], accent_colors[idx])
                if slide_index == 6:
                    table_x = 1.1
                    table_y = 1.7
                    table_w = 11.0
                    table_h = 3.9
                    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(table_x), Inches(table_y), Inches(table_w), Inches(table_h))
                    shape.fill.solid(); shape.fill.fore_color.rgb = RGBColor(16, 27, 46); shape.line.fill.background()
                    table_lines = ['Direct P2P            Cloud relay            WeTransfer             FTP/SFTP', 'Metadata only         Files stored in cloud   Public links           Server-based', 'Permissions + expiry  Links only             Large files only       Manual control']
                    for idx, line in enumerate(table_lines):
                        tb = slide.shapes.add_textbox(Inches(table_x + 0.25), Inches(table_y + table_h - 0.6 - idx * 0.7), Inches(table_w - 0.5), Inches(0.4))
                        p = tb.text_frame.paragraphs[0]
                        p.text = line
                        p.runs[0].font.size = Pt(12)
                        p.runs[0].font.color.rgb = RGBColor(255, 255, 255)

    pptx_path = path
    prs.save(pptx_path)
    return pptx_path


if __name__ == '__main__':
    output_path = r'C:\Users\PYTHON\Desktop\pythonExamples\ftp_app\FileJet_Marketing_Deck.pdf'
    pptx_path = r'C:\Users\PYTHON\Desktop\pythonExamples\ftp_app\FileJet_Marketing_Deck.pptx'
    build_pdf(output_path)
    build_pptx(pptx_path)
    print(f'PDF created: {output_path}')
    print(f'PPTX created: {pptx_path}')
