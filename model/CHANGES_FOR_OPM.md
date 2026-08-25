# Правки деки для OPM Flow

Каждая строка — потенциальный источник расхождения при контрольном
пересчёте. Нужно уметь объяснить каждую.

| Что | Где | Почему |
|---|---|---|
| блок `#if 0 ... #endif` | MODEL_Y.DATA | препроцессор tNavigator |
| `IMPLICIT` | MODEL_Y.DATA | ключевое слово tNavigator |
| CRLF -> LF | MODEL_Y.DATA | окончания строк Windows |
| лишний `/` после WELLDIMS | MODEL_Y.DATA | двойной терминатор |
| убраны WOMT/WOMR/WTHP/WEFF | SUMMARY.INC | OPM их не знает; масса считается из WOPT x 0.833 |
