#!/usr/bin/env python3
"""Build the report from Markdown and vector PlantUML diagrams.

Install: python -m pip install -r tools/requirements-pdf.txt
Run:     python tools/build_report_pdf.py
"""
from __future__ import annotations

import argparse
import copy
import html
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, HRFlowable, KeepTogether, LongTable,
    NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, TableStyle, CondPageBreak,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import String, Group
from svglib.svglib import svg2rlg

ROOT = Path(__file__).resolve().parents[1]
NAVY = colors.HexColor('#18334B')
TEXT = colors.HexColor('#202C35')
MUTED = colors.HexColor('#526779')
LINE = colors.HexColor('#CED8E1')
LIGHT = colors.HexColor('#F4F7FA')
PALE = colors.HexColor('#EAF1F8')
GREEN = colors.HexColor('#E1EFE9')
rl_config.warnOnMissingFontGlyphs = 1


def register_fonts():
    font_dir = os.environ.get('REPORT_FONT_DIR')
    roots = ([Path(font_dir).expanduser()] if font_dir else []) + [
        ROOT / 'fonts',
        Path.home() / '.local/share/fonts',
        Path.home() / '.fonts',
        Path.home() / 'Library/Fonts',
        Path('/usr/local/share/fonts'),
        Path('/usr/share/fonts'),
        Path('/Library/Fonts'),
        Path('/System/Library/Fonts/Supplemental'),
        Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts',
    ]
    font_files = {}
    for folder in roots:
        if folder.is_dir():
            for path in sorted(folder.rglob('*')):
                if path.suffix.lower() == '.ttf':
                    font_files.setdefault(path.name.casefold(), path)
    def locate(names):
        for name in names:
            path = font_files.get(name.casefold())
            if path is not None:
                return str(path)
        raise FileNotFoundError(
            'Required font not found: ' + ', '.join(names)
            + '. Install Noto Serif, Noto Sans and DejaVu Sans, '
              'or set REPORT_FONT_DIR to their font directory.'
        )
    families = {
        'Body': [('NotoSerif-Regular.ttf','Times New Roman.ttf'), ('NotoSerif-Bold.ttf','Times New Roman Bold.ttf'), ('NotoSerif-Italic.ttf','Times New Roman Italic.ttf'), ('NotoSerif-BoldItalic.ttf','Times New Roman Bold Italic.ttf')],
        'Sans': [('NotoSans-Regular.ttf','Arial.ttf'), ('NotoSans-Bold.ttf','Arial Bold.ttf'), ('NotoSans-Italic.ttf','Arial Italic.ttf'), ('NotoSans-BoldItalic.ttf','Arial Bold Italic.ttf')],
        'Mono': [('DejaVuSansMono.ttf','Courier New.ttf'), ('DejaVuSansMono-Bold.ttf','Courier New Bold.ttf'), ('DejaVuSansMono-Oblique.ttf','Courier New Italic.ttf'), ('DejaVuSansMono-BoldOblique.ttf','Courier New Bold Italic.ttf')],
        'DejaVu Sans': [('DejaVuSans.ttf',), ('DejaVuSans-Bold.ttf',), ('DejaVuSans-Oblique.ttf',), ('DejaVuSans-BoldOblique.ttf',)],
    }
    for family, variants in families.items():
        names = [family, family+'-Bold', family+'-Italic', family+'-BoldItalic']
        for name, candidates in zip(names, variants):
            pdfmetrics.registerFont(TTFont(name, locate(candidates)))
        pdfmetrics.registerFontFamily(family, normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3])
    # SVG rendering resolves the spaced family name and weight separately.
    from reportlab.lib.fonts import addMapping
    for bold, italic, name in [(0,0,'DejaVu Sans'), (1,0,'DejaVu Sans-Bold'), (0,1,'DejaVu Sans-Italic'), (1,1,'DejaVu Sans-BoldItalic')]:
        addMapping('DejaVu Sans', bold, italic, name)


def inline(text: str, code_size: float = 9.0, full_links: bool = False) -> str:
    tokens = {}
    def hold(value):
        key = f'ZZTOKEN{len(tokens)}ZZ'
        tokens[key] = value
        return key
    def code(m):
        return hold(f'<font name="Mono" size="{code_size}" color="#2D5879">{html.escape(m.group(1))}</font>')
    text = re.sub(r'`([^`]+)`', code, text)
    def link(m):
        label, url = m.groups()
        escaped = html.escape(url if full_links else label)
        if full_links:escaped='<font name="Sans" size="8.2">'+escaped+'</font>'
        if url.startswith(('https://','http://','#')):
            return hold(f'<link href="{html.escape(url, quote=True)}" color="#2D5879">{escaped}</link>')
        return hold(escaped)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, text)
    # Noto text faces do not contain every mathematical and directional symbol.
    for char in sorted(set(text)):
        if ord(char)>127 and (ord(char) not in pdfmetrics.getFont('Sans').face.charWidths or ord(char) not in pdfmetrics.getFont('Body').face.charWidths):
            if ord(char) not in pdfmetrics.getFont('DejaVu Sans').face.charWidths:
                raise ValueError('Unsupported glyph: '+repr(char))
            text=text.replace(char,hold('<font name="DejaVu Sans">'+html.escape(char)+'</font>'))
    text = html.escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', text)
    for key, value in tokens.items():
        text = text.replace(key, value)
    return text


@dataclass
class Block:
    kind: str
    text: str = ''
    level: int = 0
    anchor: str = ''
    rows: list | None = None
    lang: str = ''


def parse_markdown(text):
    lines = text.splitlines()
    out = []
    i = 0
    anchor = ''
    while i < len(lines):
        line = lines[i].strip()
        if not line or line == '---':
            i += 1; continue
        m = re.match(r'<a id="([^"]+)"></a>', line)
        if m:
            anchor = m.group(1); i += 1; continue
        m = re.match(r'^(#{1,6})\s+(.+)', line)
        if m:
            out.append(Block('heading', m.group(2), len(m.group(1)), anchor)); anchor = ''; i += 1; continue
        if line.startswith('```'):
            lang = line[3:]; code = []; i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i]); i += 1
            out.append(Block('code', '\n'.join(code), lang=lang)); i += 1; continue
        m = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', line)
        if m:
            out.append(Block('image', m.group(2))); i += 1; continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                row = [v.strip() for v in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?',v or ' ') for v in row): rows.append(row)
                i += 1
            out.append(Block('table', rows=rows)); continue
        if re.match(r'^\*(Таблица|Рисунок) ', line) and line.endswith('*'):
            out.append(Block('table_caption' if line.startswith('*Таблица') else 'figure_caption', line[1:-1])); i += 1; continue
        m = re.match(r'^(\d+\.|-)\s+(.+)',line)
        if m:
            out.append(Block('list', m.group(2), anchor=m.group(1))); i += 1; continue
        paragraph = [line]; i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith(('#','|','```','<a ','![')) or re.match(r'^(\d+\.|-)\s+',nxt) or re.match(r'^\*(Таблица|Рисунок) ',nxt): break
            paragraph.append(nxt); i += 1
        out.append(Block('paragraph', ' '.join(paragraph)))
    start = next(i for i,b in enumerate(out) if b.anchor == 'task')
    return out[start:]


class CodePanel(Flowable):
    def __init__(self, text, small=False, prepared=None, first=True, label=""):
        self.label=label
        super().__init__()
        self.raw = text
        self.small = small
        self.font_size = 7.6 if small else 8.5
        self.leading = 10.3 if small else 11.5
        self.padding = 10
        self.prepared = prepared
        self.first = first
        self.spaceBefore = 5
        self.spaceAfter = 10
    def _prepare(self, width):
        available = width - 2*self.padding
        result = []
        for line in self.raw.split('\n'):
            if not line:
                result.append(''); continue
            remainder = line.expandtabs(4)
            continuation = False
            while pdfmetrics.stringWidth(remainder,'Mono',self.font_size) > available:
                low, high = 1, len(remainder)
                while low < high:
                    mid = (low+high+1)//2
                    if pdfmetrics.stringWidth(remainder[:mid],'Mono',self.font_size) <= available: low=mid
                    else: high=mid-1
                count = low
                # Prefer a token boundary without wasting most of the line.
                preferred = max(remainder.rfind(' ',0,count),remainder.rfind(',',0,count))
                if preferred > count*0.65: count=preferred+1
                result.append(remainder[:count].rstrip())
                remainder='    '+remainder[count:].lstrip()
                continuation=True
            result.append(remainder)
        return result
    def wrap(self, availW, availH):
        self.width=availW
        if self.prepared is None: self.prepared=self._prepare(availW)
        self.height=len(self.prepared)*self.leading+2*self.padding+(14 if self.small and not self.first else 0)
        return self.width,self.height
    def split(self, availW, availH):
        if not self.small:return []
        if self.prepared is None: self.wrap(availW,availH)
        count=int((availH-2*self.padding-(14 if self.small and not self.first else 0))/self.leading)
        if count < 3:return []
        if count >= len(self.prepared):return [self]
        return [CodePanel('',self.small,self.prepared[:count],self.first,self.label),CodePanel('',self.small,self.prepared[count:],False,self.label)]
    def draw(self):
        c=self.canv
        c.setFillColor(LIGHT); c.setStrokeColor(LINE); c.setLineWidth(.35)
        c.roundRect(0,0,self.width,self.height,4,stroke=1,fill=1)
        c.setFont('Mono',self.font_size); c.setFillColor(TEXT)
        y=self.height-self.padding-self.font_size
        if self.small and not self.first:
            c.setFont('Sans',7);c.setFillColor(MUTED)
            c.drawString(self.padding,y,self.label+' · продолжение')
            c.setFont('Mono',self.font_size);c.setFillColor(TEXT);y-=14
        for line in self.prepared:
            c.drawString(self.padding,y,line)
            y-=self.leading


def preserve_svg_text_lengths(drawing, path):
    # PlantUML records the exact advances used by its layout in SVG textLength.
    # svglib omits lengthAdjust; apply an equivalent local horizontal transform.
    targets=[n for n in ET.fromstring(Path(path).read_text()).iter()
             if n.tag.endswith('text') and ''.join(n.itertext()).strip()]
    found=[]
    def collect(group):
        for index,item in enumerate(group.contents):
            if isinstance(item,String) and item.text.strip():found.append((group,index,item))
            elif hasattr(item,'contents'):collect(item)
    collect(drawing)
    if len(targets)!=len(found):
        raise ValueError('SVG text structure mismatch: '+str(path))
    for target,(parent,index,item) in zip(targets,found):
        expected=''.join(target.itertext())
        if expected.strip()!=item.text.strip():
            raise ValueError('SVG text order mismatch: '+repr(expected))
        length=target.attrib.get('textLength')
        if not length:continue
        measured=pdfmetrics.stringWidth(item.text,item.fontName,item.fontSize)
        if measured<=0:continue
        factor=float(length)/measured
        group=Group(item)
        group.transform=(factor,0,0,1,item.x*(1-factor),0)
        parent.contents[index]=group


class VectorFigure(Flowable):
    def __init__(self,path,max_width,max_height):
        super().__init__()
        self.path=path
        self.drawing=svg2rlg(str(path))
        if self.drawing is None:raise ValueError('Cannot parse SVG: '+str(path))
        preserve_svg_text_lengths(self.drawing,path)
        self.scale=min(max_width/self.drawing.width,max_height/self.drawing.height)
        self.width=max_width
        self.height=self.drawing.height*self.scale
    def draw(self):
        c=self.canv
        c.saveState()
        c.translate((self.width-self.drawing.width*self.scale)/2,0)
        c.scale(self.scale,self.scale)
        renderPDF.draw(self.drawing,c,0,0)
        c.restoreState()


class Cover(Flowable):
    def __init__(self):
        super().__init__(); self.width=A4[0]-104; self.height=A4[1]-104
    def draw(self):
        c=self.canv; w=self.width; h=self.height
        c.setStrokeColor(NAVY); c.setLineWidth(1.5); c.line(0,h-4,w,h-4)
        c.setFont('Sans-Bold',10.5); c.setFillColor(MUTED)
        c.drawString(0,h-34,'МИКРОСЕРВИСНАЯ АРХИТЕКТУРА')
        c.setFont('Sans-Bold',34); c.setFillColor(NAVY)
        c.drawString(0,h-132,'ОТЧЁТ')
        c.setFont('Sans',14.5); c.drawString(0,h-163,'по лабораторной работе № 1')
        c.setStrokeColor(LINE); c.setLineWidth(.7); c.line(0,h-194,w,h-194)
        title=Paragraph('Декомпозиция системы<br/>на микросервисы',ParagraphStyle('CoverTitle',fontName='Sans-Bold',fontSize=25,leading=32,textColor=NAVY))
        _,th=title.wrap(w,200); title.drawOn(c,0,h-230-th)
        c.setFont('Body',15); c.setFillColor(TEXT)
        c.drawString(0,h-350,'Система бронирования отелей')
        c.setFillColor(PALE); c.roundRect(0,h-435,w,52,5,stroke=0,fill=1)
        c.setFillColor(MUTED); c.setFont('Sans',10.5); c.drawString(15,h-408,'ВАРИАНТ')
        c.setFillColor(NAVY); c.setFont('Sans-Bold',18); c.drawRightString(w-16,h-415,'1115')
        c.setStrokeColor(LINE); c.line(0,57,w,57)
        c.setFillColor(MUTED); c.setFont('Sans',10)
        c.drawString(0,33,'Учебная работа по дисциплине')
        c.drawRightString(w,33,'29 октября 2026 года')


class ReportDoc(BaseDocTemplate):
    def __init__(self,path,styles):
        super().__init__(str(path),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=53,bottomMargin=43,
            title='Система бронирования отелей. Лабораторная работа № 1. Вариант 1115',
            author='',subject='Декомпозиция системы на микросервисы',creator='Микросервисная архитектура',
            pageCompression=1,allowSplitting=1)
        self.styles=styles
        self._chapter=''
        self.records=[]
        self.sizes={
            'body':(A4,52,52,53,43),
            'landscape':(landscape(A4),30,30,44,34),
            'large':(A3,47,47,51,39),
            'large_landscape':(landscape(A3),42,42,47,37),
        }
        templates=[]
        for name,(size,left,right,top,bottom) in self.sizes.items():
            frame=Frame(left,bottom,size[0]-left-right,size[1]-top-bottom,id=name+'frame',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
            templates.append(PageTemplate(name,frames=[frame],pagesize=size,onPageEnd=self.page_end))
        self.addPageTemplates(templates)
    def beforeDocument(self):
        self._chapter='Лабораторная работа № 1'
        self.records=[]
    def page_end(self,c,doc):
        w,h=c._pagesize
        name=self.pageTemplate.id
        size,left,right,top,bottom=self.sizes[name]
        c.saveState()
        c.setStrokeColor(LINE);c.setLineWidth(.45)
        c.line(left,h-31,w-right,h-31)
        c.setFillColor(MUTED);c.setFont('Sans',7.5)
        c.drawString(left,h-22,'МИКРОСЕРВИСНАЯ АРХИТЕКТУРА')
        title=self._chapter.replace('\u00a0',' ')
        fs=7.5
        limit=(w-left-right)*.57
        while pdfmetrics.stringWidth(title,'Sans',fs)>limit and fs>6.2:fs-=.2
        c.setFont('Sans',fs);c.drawRightString(w-right,h-22,title)
        c.line(left,28,w-right,28)
        c.setFont('Sans',8);c.drawString(left,15,'Лабораторная работа № 1 · Вариант 1115')
        c.setFont('Sans-Bold',9);c.drawRightString(w-right,14,str(self.page))
        c.restoreState()
    def afterFlowable(self,f):
        if isinstance(f,Paragraph) and hasattr(f,'_bookmarkName'):
            key=f._bookmarkName
            text=f.getPlainText()
            self.canv.bookmarkPage(key)
            level=getattr(f,'_outlineLevel',0)
            self.canv.addOutlineEntry(text,key,level=level,closed=level>0)
            if level==0:
                self._chapter=text
                self.notify('TOCEntry',(0,text,self.page,key))
            self.records.append({'title':text,'page':self.page,'key':key,'level':level})


def make_styles():
    common=dict(textColor=TEXT,allowWidows=0,allowOrphans=0,splitLongWords=1)
    body=ParagraphStyle('Body',fontName='Body',fontSize=10.8,leading=14.4,spaceAfter=5.8,alignment=TA_JUSTIFY,
        hyphenationLang='ru_RU',embeddedHyphenation=1,**common)
    return {
        'body':body,
        'h1':ParagraphStyle('H1',fontName='Sans-Bold',fontSize=18,leading=23,textColor=NAVY,spaceAfter=17,keepWithNext=True),
        'h2':ParagraphStyle('H2',fontName='Sans-Bold',fontSize=12.4,leading=17,textColor=NAVY,spaceBefore=13,spaceAfter=8,keepWithNext=True),
        'caption':ParagraphStyle('Caption',fontName='Sans',fontSize=9,leading=12.5,textColor=MUTED,spaceBefore=5,spaceAfter=7,keepWithNext=True),
        'figcaption':ParagraphStyle('FigCaption',fontName='Sans',fontSize=10,leading=14,textColor=MUTED,alignment=TA_CENTER,spaceBefore=9,spaceAfter=11),
        'cell':ParagraphStyle('Cell',fontName='Sans',fontSize=8.8,leading=11.8,textColor=TEXT,splitLongWords=1,allowWidows=0,allowOrphans=0),
        'headcell':ParagraphStyle('HeadCell',fontName='Sans-Bold',fontSize=9,leading=12.1,textColor=NAVY,splitLongWords=1),
        'labelcell':ParagraphStyle('LabelCell',fontName='Sans-Bold',fontSize=8.7,leading=12,textColor=NAVY,splitLongWords=1),
        'list':ParagraphStyle('List',parent=body,leftIndent=20,firstLineIndent=0,bulletIndent=2,bulletFontName='Sans',bulletFontSize=9.6,spaceAfter=5),
        'small':ParagraphStyle('Small',fontName='Sans',fontSize=8.8,leading=12.2,textColor=MUTED,spaceAfter=7),
        'toc':ParagraphStyle('TOC',fontName='Sans',fontSize=10.2,leading=15.2,textColor=TEXT,leftIndent=0,firstLineIndent=0,rightIndent=20,spaceBefore=7),
        'toc_title':ParagraphStyle('TOCTitle',fontName='Sans-Bold',fontSize=22,leading=28,textColor=NAVY,spaceAfter=25),
    }


WIDTHS={1:[.27,.26,.47],2:[.30,.70]}


def table_flow(block,caption,width,styles):
    ncols=len(block.rows[0])
    m=re.search(r'Таблица (\d+) ',caption)
    ratios=WIDTHS.get(int(m.group(1))) if m else [.28,.72] if ncols==2 else None
    if ratios is None or len(ratios)!=ncols:ratios=[1/ncols]*ncols
    data=[]
    attr='Таблица А.' in caption
    for row_i,row in enumerate(block.rows):
        cells=[]
        for col_i,text in enumerate(row):
            style=styles['headcell'] if row_i==0 else styles['labelcell'] if attr and col_i==0 else styles['cell']
            if attr and row_i>0:
                style=copy.copy(style);style.fontSize=9.1 if col_i else 9.0;style.leading=12.8
            if col_i>0 and re.fullmatch(r'[\d\s,.%*]+',text):
                style=copy.copy(style);style.alignment=TA_RIGHT
            cells.append(Paragraph(inline(text,7.6),style))
        data.append(cells)
    t=LongTable(data,colWidths=[r*width for r in ratios],repeatRows=1,hAlign='LEFT',spaceAfter=11)
    commands=[('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),PALE),
              ('LINEABOVE',(0,0),(-1,0),.7,colors.HexColor('#8FA7BB')),
              ('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#8FA7BB')),
              ('LINEBELOW',(0,1),(-1,-1),.28,LINE),
              ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
              ('TOPPADDING',(0,0),(-1,0),7),('BOTTOMPADDING',(0,0),(-1,0),7),
              ('TOPPADDING',(0,1),(-1,-1),6 if attr else 5.2),('BOTTOMPADDING',(0,1),(-1,-1),6 if attr else 5.2),
              ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F7F9FB')])]
    if 'Итого' in ' '.join(block.rows[-1]):commands.append(('BACKGROUND',(0,len(data)-1),(-1,-1),GREEN))
    t.setStyle(TableStyle(commands));return t


def heading(block,styles,keycount):
    major=block.level==2
    p=Paragraph(inline(block.text),styles['h1' if major else 'h2'])
    p._bookmarkName=block.anchor or f'section-{keycount}'
    p._outlineLevel=0 if major else 1
    return p


def build(output):
    protected=ROOT/'output/pdf/LR1_Variant_1115.pdf'
    if Path(output).resolve()==protected.resolve():
        raise ValueError('The previous PDF is preserved. Use a different output filename.')
    register_fonts()
    styles=make_styles()
    styles['h1'].fontSize=14.5;styles['h1'].leading=19
    styles['h1'].spaceBefore=12;styles['h1'].spaceAfter=9
    blocks=parse_markdown((ROOT/'LR1_Variant_1115.md').read_text())
    doc=ReportDoc(output,styles)
    title_style=ParagraphStyle('ReportTitle',fontName='Sans-Bold',fontSize=21,leading=26,textColor=NAVY,spaceAfter=9)
    story=[Paragraph('Лабораторная работа № 1 · Вариант 1115',styles['small']),
           Paragraph('Декомпозиция системы на микросервисы',title_style),
           Paragraph('Микросервисная архитектура · Система бронирования отелей',styles['small'])]
    count=0
    section=''
    caption=''
    width=A4[0]-104

    def flow(block):
        nonlocal count,caption
        if block.kind=='heading':
            count+=1
            return heading(block,styles,count)
        if block.kind=='paragraph':return Paragraph(inline(block.text),styles['body'])
        if block.kind=='list':return Paragraph(inline(block.text),styles['list'],bulletText='•' if block.anchor=='-' else block.anchor)
        if block.kind=='code':return CodePanel(block.text)
        if block.kind=='table_caption':
            caption=block.text
            return Paragraph(inline(block.text),styles['caption'])
        if block.kind=='table':return table_flow(block,caption,width,styles)
        return None

    i=0
    while i<len(blocks):
        b=blocks[i]
        if b.kind=='paragraph' and b.text.startswith('[PlantUML]'):
            i+=1;continue
        if b.kind=='heading' and b.level==2:
            section=b.anchor
            story.append(CondPageBreak(160))
            story.append(flow(b));i+=1;continue
        if section=='catalog' and b.kind=='heading' and b.level==3:
            j=i+1
            while j<len(blocks) and blocks[j].kind!='heading':j+=1
            items=[flow(item) for item in blocks[i:j]]
            items=[item for item in items if item is not None]
            height=0
            for item in items:
                _,h=item.wrap(width,9999)
                height+=h+item.getSpaceBefore()+item.getSpaceAfter()
            story.append(CondPageBreak(min(height+6,A4[1]-115)))
            story.extend(items);i=j;continue
        if b.kind=='image':
            label=blocks[i+1]
            assert label.kind=='figure_caption'
            prefix=[]
            if i>0 and blocks[i-1].kind=='heading':prefix=[story.pop()]
            post=[];j=i+2
            while j<len(blocks) and blocks[j].kind=='paragraph' and blocks[j].text.startswith('[PlantUML]'):j+=1
            if 'c4-applications' in b.text:
                while j<len(blocks) and blocks[j].kind=='paragraph' and not blocks[j].text.startswith('[PlantUML]'):
                    post.append(blocks[j]);j+=1
            template='landscape' if 'context-map' in b.text else 'large'
            add_figure(story,doc,styles,b.text,label.text,prefix,post,template,count)
            i=j;continue
        item=flow(b)
        if item is not None:story.append(item)
        i+=1
    while story and isinstance(story[-1],(PageBreak,NextPageTemplate)):story.pop()
    doc.build(story)
    return doc.records


def add_figure(story,doc,styles,path,caption,prefix,after,template,keycount):
    size,left,right,top,bottom=doc.sizes[template]
    width=size[0]-left-right
    height=size[1]-top-bottom
    # Avoid a blank body page when the previous operation already requested a break.
    if story and isinstance(story[-1],PageBreak):story.pop()
    story.extend([NextPageTemplate(template),PageBreak()])
    prefix_flows=[]
    for n,item in enumerate(prefix):
        if isinstance(item,Block):prefix_flows.append(heading(item,styles,keycount+n+1))
        else:prefix_flows.append(item)
    post=[Paragraph(inline(x.text),styles['body']) for x in after]
    cap=Paragraph(inline(caption),styles['figcaption'])
    reserved=30
    for f in prefix_flows+[cap]+post:
        _,h=f.wrap(width,9999)
        reserved+=h+f.getSpaceBefore()+f.getSpaceAfter()
    fig=VectorFigure(ROOT/path,width,height-reserved)
    story.extend(prefix_flows+[Spacer(0,7),fig,cap]+post)
    story.extend([NextPageTemplate('body'),PageBreak()])


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',type=Path,default=ROOT/'output/pdf/LR1_Variant_1115_Revised.pdf')
    args=ap.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    records=build(args.output)
    print(args.output)
