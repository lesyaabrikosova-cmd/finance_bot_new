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
        items = items[:7] + [('Остальное', sum((v for _, v in items[7:]), Decimal(0)))]
    return items


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
    im = Image.new('RGB', (1080, 830 + 105 * legend_rows), '#191321')
    draw = ImageDraw.Draw(im)
    font_dir = Path(__file__).resolve().parent / 'assets/fonts'
    def font(size, bold=False):
        return ImageFont.truetype(str(font_dir / ('PTSans-Bold.ttf' if bold else 'PTSans-Regular.ttf')), size)
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
        y = 830 + row * 105
        draw.rounded_rectangle((x, y+8, x+30, y+38), 6, fill=used[i])
        label_font = font(36)
        while draw.textlength(label, font=label_font) > 450 and len(label) > 1:
            label = label[:-2] + '…'
        draw.text((x+52, y), label, font=label_font, fill='#F9F4ED')
        percent = value / (center_amount if center_amount is not None and center_amount > 0 else total) * 100
        percent_text = '<0,1' if percent < Decimal('0.1') else f'{percent:.1f}'.replace('.', ',')
        amount = f'{value:,.2f}'.replace(',', ' ').replace('.', ',')
        if amount.endswith(',00'):
            amount = amount[:-3]
        draw.text((x+52, y+45), f'{percent_text}%' if percentages_only else f'{amount} ₽  ·  {percent_text}%', font=font(34, True), fill='#F1CD83')
    out = BytesIO()
    im.save(out, 'PNG', optimize=True)
    return out.getvalue()


async def send_chart_report(message, values, title, text, reply_markup=None, subtitle='', colors=None,
                            percentages_only=False, fallback_text=None, **chart_options):
    try:
        data = await asyncio.to_thread(make_chart, values, title, subtitle, colors, percentages_only, **chart_options)
    except (ImportError, OSError):
        data = None
    if data:
        await message.answer_photo(photo=BufferedInputFile(data, filename='report.png'),
                                   caption=text if len(text) <= 1024 else title,
                                   reply_markup=reply_markup if len(text) <= 1024 else None)
    if not data:
        await message.answer(fallback_text or text, reply_markup=reply_markup)
    elif len(text) > 1024:
        await message.answer(text, reply_markup=reply_markup)
