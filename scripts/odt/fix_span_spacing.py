# -*- coding: utf-8 -*-
"""Убирает 'рискованные' стыки спанов в ODT-шаблоне.

report-generator сохраняет content.xml с отступами; перенос строки между
</text:span> и <text:span> внутри абзаца рендерится как пробел. Поэтому в
шаблоне на стыке спанов всегда должен быть либо обычный пробел (лишний
схлопнется), либо стыка не должно быть вовсе.
"""
import re, sys
from lxml import etree

T = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
FO = '{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}'
ST = '{urn:oasis:names:tc:opendocument:xmlns:style:1.0}'
CLOSERS = '.,;:)»!?'
OPENERS = '(«'
WORD = re.compile(r'[\w@%№-]', re.U)
VIS = (FO+'font-weight', FO+'font-style', ST+'text-underline-style', FO+'font-size',
       FO+'background-color', ST+'font-name', ST+'text-position')

def load_styles(root):
    st = {}
    for s in root.iter(ST+'style'):
        if s.get(ST+'family') != 'text':
            continue
        props = {}
        for p in s:
            props.update(p.attrib)
        key = tuple(props.get(k) for k in VIS) + (props.get(FO+'color', '#000000').lower(),)
        st[s.get(ST+'name')] = key
    return st

def full_text(el):
    if el.tag == T+'s':
        return ' ' * int(el.get(T+'c', 1))
    if el.tag == T+'tab':
        return '\t'
    if el.tag == T+'line-break':
        return '\n'
    out = []
    if el.text: out.append(el.text)
    for ch in el:
        out.append(full_text(ch))
        if ch.tail: out.append(ch.tail)
    return ''.join(out)

def append_text(el, s):
    if len(el): el[-1].tail = (el[-1].tail or '') + s
    else: el.text = (el.text or '') + s

def strip_leading(el, n):
    if el.text is not None and len(el.text) >= n:
        el.text = el.text[n:]; return
    strip_leading(el[0], n)

def strip_trailing(el, n):
    if len(el):
        last = el[-1]
        if last.tail and len(last.tail) >= n:
            last.tail = last.tail[:-n]; return
        strip_trailing(last, n); return
    el.text = el.text[:-n]

def prepend_text(el, s):
    if el.text is not None: el.text = s + el.text
    else: prepend_text(el[0], s)

def merge(a, b):
    append_text(a, b.text or '')
    for ch in list(b): a.append(ch)
    tail = b.tail
    b.getparent().remove(b)
    if tail: append_text(a, tail)


def normalize_explicit_spaces(root, log):
    """<text:s/> — неразрывный «жёсткий» пробел: вставленный генератором пробел
    с ним не схлопнется. На стыках и рядом с обычным пробелом заменяем/убираем."""
    for sp in list(root.iter(T+'s')):
        if sp.get(T+'c') not in (None, '1'):
            continue
        par = sp.getparent(); prev = sp.getprevious()
        before = (prev.tail if prev is not None else par.text) or ''
        after = sp.tail or ''
        if before.endswith((' ', '\xa0')) or after.startswith((' ', '\xa0')):
            action = 'drop'
        else:
            action = 'plain'
        tail = sp.tail or ''
        repl = ' ' if action == 'plain' else ''
        if prev is not None:
            prev.tail = (prev.tail or '') + repl + tail
        else:
            par.text = (par.text or '') + repl + tail
        par.remove(sp)
        log.append(('text:s→' + action, before[-30:], after[:30]))

def fix(src, dst):
    tree = etree.parse(src); root = tree.getroot()
    styles = load_styles(root)
    log = []
    normalize_explicit_spaces(root, log)
    for p in root.iter(T+'p'):
        kids = list(p); i = 0
        while i < len(kids) - 1:
            a, b = kids[i], kids[i+1]
            ta, tb = full_text(a), full_text(b)
            if a.tail or ta == '' or tb == '' or ta.endswith('\n') or tb.startswith('\n'):
                i += 1; continue
            ctx = (ta[-38:], tb[:38])
            if tb.startswith('\xa0'):
                strip_leading(b, 1); append_text(a, ' ')
                log.append(('nbsp→space', *ctx))
            elif ta.endswith('\xa0'):
                strip_trailing(a, 1); append_text(a, ' ')
                log.append(('nbsp→space', *ctx))
            elif ta.endswith((' ', '\t')) or tb.startswith((' ', '\t')):
                pass
            elif tb[0] in CLOSERS:
                n = len(re.match('['+re.escape(CLOSERS)+']+', tb).group(0))
                if n == len(tb):
                    merge(a, b); kids = list(p)
                    log.append(('merge-punct', *ctx)); continue
                strip_leading(b, n); append_text(a, tb[:n])
                log.append(('move-punct→left', *ctx))
            elif ta[-1] in OPENERS:
                n = len(re.search('['+re.escape(OPENERS)+']+$', ta).group(0))
                strip_trailing(a, n); prepend_text(b, ta[-n:])
                log.append(('move-punct→right', *ctx))
            elif styles.get(a.get(T+'style-name')) == styles.get(b.get(T+'style-name')) \
                 and a.tag == b.tag == T+'span':
                merge(a, b); kids = list(p)
                log.append(('merge-samestyle', *ctx)); continue
            elif WORD.match(ta[-1]) and WORD.match(tb[0]) and a.tag == b.tag == T+'span':
                merge(a, b); kids = list(p)
                log.append(('merge-word', *ctx)); continue
            else:
                log.append(('LEFT-AS-IS', *ctx))
            i += 1
    tree.write(dst, xml_declaration=True, encoding='UTF-8')
    return log

if __name__ == '__main__':
    log = fix(sys.argv[1], sys.argv[2])
    from collections import Counter
    print(Counter(k for k, *_ in log))
    for k, l, r in log:
        print(f'{k:18} …{l!r} || {r!r}')
