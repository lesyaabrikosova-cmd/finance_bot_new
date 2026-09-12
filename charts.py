"""Personal report charts; amounts, labels and colors share one data source."""
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import asyncio
from aiogram.types import BufferedInputFile

PALETTE = ('#9675E5', '#55B5DB', '#E5B65B', '#69BE98', '#E68091', '#98A8EF', '#CE91D1', '#B6BA65')


def chart_items(values, preserve_order=False):
    items = [(str(k), Decimal(str(v))) for k, v in values.items() if Decimal(str(v)) > 0]
    if preserve_order:
        return items
    items.sort(key=lambda item: item[1], reverse=True)
    if len(items) > 8:
        remainder = items[7:]
        remainder_label = 'Остальное: ' + ', '.join(label for label, _ in remainder)
        items = items[:7] + [(remainder_label, sum((v for _, v in remainder), Decimal(0)))]
    return items


def wrap_legend_label(label, max_width, measure):
    """Wrap a legend label without dropping any identifying part of its text."""
    if max_width <= 0:
        raise ValueError('max_width must be positive')
    remaining = ' '.join(str(label).split())
    if not remaining:
        return ('',)
    lines = []
    while measure(remaining) > max_width:
        low, high, fitting = 1, len(remaining), 0
        while low <= high:
            middle = (low + high) // 2
            if measure(remaining[:middle]) <= max_width:
                fitting = middle
                low = middle + 1
            else:
                high = middle - 1
        # A single glyph can be wider than the requested width. Keeping it is
        # preferable to losing part of an account, goal or income source name.
        fitting = max(fitting, 1)
        word_boundary = remaining.rfind(' ', 0, fitting + 1)
        if word_boundary > 0:
            lines.append(remaining[:word_boundary])
            remaining = remaining[word_boundary + 1:].lstrip()
        else:
            lines.append(remaining[:fitting])
            remaining = remaining[fitting:].lstrip()
    lines.append(remaining)
    return tuple(lines)


def report_fallback_text(title, text, fallback_text=None):
    """Return non-empty text for a report when its image cannot be delivered."""
    for candidate in (fallback_text, text, title):
        if candidate and str(candidate).strip():
            return candidate
    return 'Отчёт временно недоступен.'


def make_chart(
    values,
    title,
    subtitle='',
    colors=None,
    percentages_only=False,
    *,
    preserve_order=False,
    center_amount=None,
    center_label='Доход за период',
    center_suffix='₽ до налогов',
):
    from PIL import Image, ImageDraw, ImageFont
    items = chart_items(values, preserve_order)
    if not items:
        return None
    total = sum((v for _, v in items), Decimal(0))
    legend_rows = (len(items) + 1) // 2
    font_dir = Path(__file__).resolve().parent / 'assets/fonts'
    def font(size, bold=False):
        return ImageFont.truetype(str(font_dir / ('PTSans-Bold.ttf' if bold else 'PTSans-Regular.ttf')), size)
    label_font = font(36)
    measure_image = Image.new('RGB', (1, 1), '#191321')
    measure_draw = ImageDraw.Draw(measure_image)
    legend_lines = [
        wrap_legend_label(label, 440, lambda value: measure_draw.textlength(value, font=label_font))
        for label, _ in items
    ]
    row_heights = []
    for row in range(legend_rows):
        row_indexes = (row, row + legend_rows)
        line_counts = [len(legend_lines[index]) for index in row_indexes if index < len(items)]
        row_heights.append(max(line_counts) * 42 + 63)
    row_tops = []
    next_top = 830
    for row_height in row_heights:
        row_tops.append(next_top)
        next_top += row_height
    im = Image.new('RGB', (1080, next_top + 10), '#191321')
    draw = ImageDraw.Draw(im)
    draw.text((45, 30), title, font=font(54, True), fill='#F1CD83')
    draw.text((45, 110), subtitle, font=font(30), fill='#D0C2D8')
    start = -90
    used = []
    for i, (label, value) in enumerate(items):
        color = (colors or {}).get(label, PALETTE[i % len(PALETTE)])
        used.append(color)
        end = start + float(value / total * 360)
        draw.pieslice((240, 190, 840, 790), start, end, fill=color)
        start = end
    draw.ellipse((395, 345, 685, 635), fill='#191321')
    if center_amount is None:
        draw.text((540, 465), '100%', anchor='mm', font=font(58, True), fill='#F9F4ED')
    else:
        center = f'{center_amount:,.2f}'.replace(',', ' ').replace('.', ',')
        if center.endswith(',00'):
            center = center[:-3]
        size = 44
        while draw.textlength(center, font=font(size, True)) > 258 and size > 14:
            size -= 1
        draw.text((540, 435), center_label, anchor='mm', font=font(27), fill='#D0C2D8')
        draw.text((540, 485), center, anchor='mm', font=font(size, True), fill='#F9F4ED')
        draw.text((540, 530), center_suffix, anchor='mm', font=font(27), fill='#D0C2D8')
    for i, (label, value) in enumerate(items):
        col, row = divmod(i, legend_rows)
        x = 48 + col * 520
        y = row_tops[row]
        draw.rounded_rectangle((x, y+8, x+30, y+38), 6, fill=used[i])
        for line_number, line in enumerate(legend_lines[i]):
            draw.text((x+52, y + line_number * 42), line, font=label_font, fill='#F9F4ED')
        percent = value / (center_amount if center_amount is not None and center_amount > 0 else total) * 100
        percent_text = '<0,1' if percent < Decimal('0.1') else f'{percent:.1f}'.replace('.', ',')
        amount = f'{value:,.2f}'.replace(',', ' ').replace('.', ',')
        if amount.endswith(',00'):
            amount = amount[:-3]
        amount_y = y + len(legend_lines[i]) * 42 + 4
        draw.text((x+52, amount_y), f'{percent_text}%' if percentages_only else f'{amount} ₽  ·  {percent_text}%', font=font(34, True), fill='#F1CD83')
    out = BytesIO()
    im.save(out, 'PNG', optimize=True)
    return out.getvalue()


async def send_chart_report(message, values, title, text, reply_markup=None, subtitle='', colors=None,
                            percentages_only=False, fallback_text=None, **chart_options):
    try:
        data = await asyncio.to_thread(make_chart, values, title, subtitle, colors, percentages_only, **chart_options)
    except Exception:
        await message.answer(report_fallback_text(title, text, fallback_text), reply_markup=reply_markup)
        return
    if not data:
        await message.answer(report_fallback_text(title, text, fallback_text), reply_markup=reply_markup)
        return
    try:
        await message.answer_photo(photo=BufferedInputFile(data, filename='report.png'),
                                   caption=text if len(text) <= 1024 else title,
                                   reply_markup=reply_markup if len(text) <= 1024 else None)
    except Exception:
        await message.answer(report_fallback_text(title, text, fallback_text), reply_markup=reply_markup)
        return
    if len(text) > 1024:
        await message.answer(text, reply_markup=reply_markup)
