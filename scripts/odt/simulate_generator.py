"""Собираем .odt так, как это делает report-generator (XML с отступами),
чтобы увидеть в рендере ровно те же лишние пробелы."""
import sys, zipfile
from lxml import etree
src_odt, content_xml, out_odt = sys.argv[1], sys.argv[2], sys.argv[3]
tree = etree.parse(content_xml)
etree.indent(tree, space='  ')          # не трогает mixed content — как .NET XmlWriter
data = etree.tostring(tree, xml_declaration=True, encoding='UTF-8')
zin = zipfile.ZipFile(src_odt); zout = zipfile.ZipFile(out_odt, 'w')
for info in zin.infolist():
    payload = data if info.filename == 'content.xml' else zin.read(info.filename)
    ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    ni.compress_type = info.compress_type; ni.external_attr = info.external_attr
    zout.writestr(ni, payload)
zout.close()
print('ok', out_odt)
