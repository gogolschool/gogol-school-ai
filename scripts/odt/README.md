# Пробелы в печатных формах Озмы (.odt)

**Симптом.** В сгенерированном документе появляются лишние пробелы: «дом 1 .»,
«15:30 .», «2.1.  В случае», «E - mail info @ gogol . school».
В самом шаблоне их нет.

**Причина.** `ozma-report-generator` сохраняет `content.xml` с отступами
(pretty-print). Перенос строки между `</text:span>` и `<text:span>` внутри
абзаца при рендере превращается в пробел. Поэтому на стыке спанов в шаблоне
должен быть либо обычный пробел (лишний схлопнётся с ним), либо стыка не должно
быть вовсе. Опасны: стык без пробела (перед точкой/запятой/скобкой),
неразрывный пробел `\xa0` на стыке и `<text:s/>` — они со вставленным пробелом
не схлопываются.

## Использование

```bash
# 1. распаковать шаблон
unzip -o template.odt content.xml -d work/
# 2. починить стыки
python3 fix_span_spacing.py work/content.xml content_fixed.xml
# 3. проверить: собрать .odt так же, как это делает генератор, и отрендерить
python3 simulate_generator.py template.odt content_fixed.xml sim.odt
soffice --headless --convert-to "txt:Text (encoded):UTF8" sim.odt
grep -nE " [.,;:)»]" sim.txt        # должно быть пусто
```

Затем собрать финальный `.odt` (порядок записей и `compress_type` как в
оригинале, `content.xml` с BOM) и залить в БД `ozma-report-generator`,
таблица `"ReportTemplates"`, поле `OdtWithoutQueries` — см. память
`reference_ozma_doc_templates`.

Требуется `python3 -m pip install lxml` и LibreOffice для проверки.
