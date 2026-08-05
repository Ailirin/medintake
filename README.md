# MedXML Validator

Утилита автоматической валидации медицинских XML-документов на Python.

Проверяет документы по XSD-схеме и дополнительным бизнес-правилам (даты, СНИЛС, диагнозы). Сокращает объём ручной проверки документов.

## Возможности

- XSD-валидация структуры и форматов полей
- Проверка обязательных полей (включая непустые значения)
- Бизнес-правила: контрольная сумма СНИЛС, даты, основной диагноз, дубликаты МКБ-10
- Уникальность `DocumentId` при пакетной проверке папки
- Понятные сообщения об ошибках на русском языке
- CLI для проверки одного файла или целой папки
- Экспорт отчёта в JSON и CSV
- Автотесты на pytest

## Структура проекта

```
Utility/
├── medxml_validator/       # Исходный код
│   ├── __init__.py         # Версия пакета
│   ├── validator.py        # Ядро: XSD + бизнес-правила
│   └── cli.py              # Запуск из командной строки
├── schemas/
│   └── medical_document.xsd  # XSD-схема документа
├── example/
│   ├── valid.xml           # Пример корректного документа
│   └── invalid.xml         # Пример с ошибками
├── tests/
│   └── test_validator.py   # Автотесты
├── requirements.txt
├── pyproject.toml
└── README.md
```

## Установка

```bash
# Клонировать / открыть проект
cd Utility

# Создать виртуальное окружение
python -m venv venv

# Активировать (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Установить зависимости
pip install -r requirements.txt
```

## Использование

### Проверка одного файла

```bash
python -m medxml_validator.cli example/valid.xml
```

### Проверка всех XML в папке

```bash
python -m medxml_validator.cli example/
```

### Своя XSD-схема

```bash
python -m medxml_validator.cli example/valid.xml --schema schemas/medical_document.xsd
```

### Экспорт отчёта в JSON

```bash
python -m medxml_validator.cli example/ --format json --output report.json
```

### Экспорт отчёта в CSV

```bash
python -m medxml_validator.cli example/ --format csv --output report.csv
```

### Пример вывода

```
[OK] example\valid.xml

[FAILED] example\invalid.xml
  [ERROR] XSD_VALIDATION: Элемент «Gender»: значение «X» недопустимо... (строка 11)
  [ERROR] XSD_VALIDATION: Элемент «Snils»: значение не соответствует формату... (строка 12)
  [ERROR] XSD_VALIDATION: Элемент «Icd10Code»: значение не соответствует формату... (строка 15)

Итого: 1/2 файлов прошли проверку
```

## Тесты

```bash
pytest tests/ -v
```

## Формат XML-документа

```xml
<MedicalDocument version="1.0">
  <DocumentId>DOC-2024-001</DocumentId>
  <DocumentDate>2024-06-15</DocumentDate>
  <OrganizationCode>ORG-001</OrganizationCode>
  <Patient>
    <LastName>Иванов</LastName>
    <FirstName>Иван</FirstName>
    <BirthDate>1985-03-20</BirthDate>
    <Gender>M</Gender>
    <Snils>112-233-445 95</Snils>
  </Patient>
  <Diagnosis>
    <Icd10Code>J06.9</Icd10Code>
    <Description>ОРВИ</Description>
    <IsPrimary>true</IsPrimary>
  </Diagnosis>
</MedicalDocument>
```

## Что проверяет валидатор

| Уровень | Проверка |
|---------|----------|
| XSD | Структура, обязательные поля, формат пола, СНИЛС, МКБ-10, даты |
| Бизнес-правила | Пустые обязательные поля, дата не в будущем, возраст ≤ 120, контрольная сумма СНИЛС, один основной диагноз, уникальные коды МКБ-10 |
| Пакетная проверка | Уникальность `DocumentId` между файлами в одной папке |

## Коды ошибок

| Код | Описание |
|-----|----------|
| `XML_SYNTAX` | Синтаксическая ошибка XML |
| `XSD_VALIDATION` | Несоответствие XSD-схеме |
| `EMPTY_REQUIRED_FIELD` | Обязательное поле пустое |
| `FUTURE_DOCUMENT_DATE` | Дата документа в будущем |
| `INVALID_SNILS_CHECKSUM` | Неверная контрольная сумма СНИЛС |
| `PRIMARY_DIAGNOSIS_COUNT` | Не ровно один основной диагноз |
| `DUPLICATE_ICD10` | Повторяющийся код МКБ-10 |
| `DUPLICATE_DOCUMENT_ID` | Одинаковый идентификатор в разных файлах |

## Технологии

- Python 3.10+
- lxml — парсинг XML и XSD-валидация
- pytest — автотесты
