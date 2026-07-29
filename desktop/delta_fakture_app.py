from __future__ import annotations

import csv
import ctypes
import hashlib
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import unicodedata
import webbrowser
import platform
import tkinter as tk
from email.message import EmailMessage
from email.utils import formataddr
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, ImageTk

from delta_fakture_core import (
    APP_NAME,
    APP_DIR,
    ASSETS_DIR,
    Database,
    PlanLimitError,
    DEFAULT_CURRENCY,
    DEFAULT_EXCHANGE_RATE,
    DEFAULT_PAYMENT_TERM_DAYS,
    DEFAULT_SMTP_PORT,
    SMTP_SECURITY_OPTIONS,
    LOGO_FILE,
    TEMPLATE_XLSX,
    PAYMENT_METHOD_OPTIONS,
    PROJECT_COST_GROUPS,
    PROJECT_INCOME_GROUPS,
    STATUS_CODES,
    STATUS_LABELS,
    INVOICE_KINDS,
    INVOICE_KIND_LABELS,
    UNIT_OPTIONS,
    CATEGORY_OPTIONS,
    COUNTRY_VAT_DEFAULTS,
    SUPPORTED_CURRENCIES,
    calculate_invoice_totals,
    decimal_from,
    default_currency_for_country,
    default_vat_rate_for_country,
    format_currency,
    money_round,
    format_date,
    get_root_dir,
    invoice_dir,
    number_to_words_bg,
    parse_date,
    payment_method_default,
    project_invoice_number,
    normalize_country_code,
    normalize_invoice_kind,
    safe_filename,
    status_label,
)
from delta_fakture_export import (
    export_credit_note_bundle,
    export_invoice_bundle,
    export_invoice_pdf,
    export_invoice_xlsx,
    export_project_accountant_bundle,
    export_project_vat_evidence_bundle,
)
from opsnest_einvoice import (
    bulgaria_en16931_readiness,
    einvoice_readiness,
    export_ubl_21_draft,
    sef_readiness,
)
from opsnest_einvoice_providers import provider_for_country
from opsnest_financial_advisor import ai_financial_summary, financial_insights
from opsnest_sef_api import SefApiError, get_sef_version
from delta_fakture_bank import read_bank_statement, statement_file_hash
from delta_fakture_mail import build_invoice_email_defaults, send_invoice_email, send_message_via_smtp
from delta_fakture_pdf import PdfInvoiceReadError, extract_invoice_fields_from_pdf, match_known_partner
from opsnest_cloud_client import CloudApiError, OpsNestCloudClient
from opsnest_plans import PLAN_CATALOG, plan_details


BG = "#F2F6FA"
PANEL = "#FFFFFF"
ACCENT = "#087E72"
ACCENT_2 = "#06675F"
HEADER_BG = "#E9F6F3"
HEADER_BG_2 = "#D3ECE7"
MUTED = "#64748B"
LINE = "#D5E0EA"
TEXT = "#0F172A"
SOFT = "#E7EEF5"
ATTACHMENT_TYPE_OPTIONS = ["Prilog", "Ugovor", "Situacija", "Obračun", "Akt 19", "Faktura", "Drugo"]


def today_iso() -> str:
    """Return today's date in the format expected by all operational forms."""
    return date.today().isoformat()


UI_LANGUAGE_LABELS = {
    "sr": "Srpski",
    "en": "English",
    "de": "Deutsch",
    "bg": "Български",
    "ru": "Русский",
}
INVOICE_DOCUMENT_LANGUAGE_LABELS = {
    "sr": "Srpski (latinica)",
    "bg": "Български",
    "en": "English",
}
SUBSCRIPTION_COPY = {
    "sr": {
        "trial": "Besplatni probni period: još {days} dana. Kartica nije potrebna.",
        "trial_last_day": "Besplatni probni period: poslednji dan. Kartica nije potrebna.",
        "expired": "Samo za pregled: probni period je istekao. Štampa, PDF/Excel izvoz i backup ostaju dostupni.",
        "not_started": "Registrujte firmu i dobijate 7 dana besplatno, bez kartice.",
        "legacy": "Lokalni pristup je aktivan. Postojeći podaci ostaju dostupni bez prekida.",
        "active": "Pretplata je aktivna: paket {plan}.",
    },
    "en": {
        "trial": "Free trial: {days} days remaining. No card required.",
        "trial_last_day": "Free trial: final day. No card required.",
        "expired": "Read-only mode: the free trial ended. Printing, PDF/Excel export and backup remain available.",
        "not_started": "Register your company to get 7 free days with no card required.",
        "legacy": "Local access is active. Existing data remains available without interruption.",
        "active": "Subscription is active: {plan} plan.",
    },
    "de": {
        "trial": "Kostenlose Testphase: noch {days} Tage. Keine Karte erforderlich.",
        "trial_last_day": "Kostenlose Testphase: letzter Tag. Keine Karte erforderlich.",
        "expired": "Nur-Lese-Modus: Die Testphase ist abgelaufen. Drucken, PDF/Excel-Export und Backup bleiben verfügbar.",
        "not_started": "Registrieren Sie Ihre Firma und erhalten Sie 7 kostenlose Tage ohne Karte.",
        "legacy": "Der lokale Zugriff ist aktiv. Vorhandene Daten bleiben ohne Unterbrechung verfügbar.",
        "active": "Das Abonnement ist aktiv: Paket {plan}.",
    },
    "bg": {
        "trial": "Безплатен пробен период: остават {days} дни. Не е нужна карта.",
        "trial_last_day": "Безплатен пробен период: последен ден. Не е нужна карта.",
        "expired": "Режим само за преглед: пробният период изтече. Печатът, PDF/Excel износът и backup остават достъпни.",
        "not_started": "Регистрирайте фирмата и получавате 7 безплатни дни без карта.",
        "legacy": "Локалният достъп е активен. Съществуващите данни остават достъпни без прекъсване.",
        "active": "Абонаментът е активен: пакет {plan}.",
    },
    "ru": {
        "trial": "Бесплатный пробный период: осталось {days} дн. Карта не требуется.",
        "trial_last_day": "Бесплатный пробный период: последний день. Карта не требуется.",
        "expired": "Режим просмотра: пробный период завершён. Печать, экспорт PDF/Excel и резервное копирование доступны.",
        "not_started": "Зарегистрируйте компанию и получите 7 бесплатных дней без карты.",
        "legacy": "Локальный доступ активен. Существующие данные остаются доступными без перерыва.",
        "active": "Подписка активна: пакет {plan}.",
    },
}
UI_TRANSLATIONS = {
    "en": {
        "Fakture, kupci, projekti i naplate": "Invoices, customers, projects and payments",
        "Šablon fakture": "Invoice template",
        "Nova faktura": "New invoice",
        "Osveži": "Refresh",
        "Smanji": "Minimize",
        "Uvećaj": "Maximize",
        "Vrati": "Restore",
        "Dashboard": "Dashboard",
        "Fakture": "Invoices",
        "Kupci": "Customers",
        "Projekti": "Projects",
        "Firma": "Company",
        "Firma i projekti": "Company and projects",
        "Backup": "Backup",
        "Podaci firme": "Company details",
        "Dopuni podatke firme": "Complete company details",
        "Podešavanja fakture": "Invoice settings",
        "Slanje e-mailom (SMTP)": "E-mail sending (SMTP)",
        "Naziv": "Name",
        "EIK / BULSTAT": "EIK / BULSTAT",
        "PDV broj": "VAT number",
        "Adresa": "Address",
        "Telefon": "Phone",
        "E-mail": "E-mail",
        "Banka": "Bank",
        "BIC / SWIFT": "BIC / SWIFT",
        "Direktor": "Director",
        "Logo putanja": "Logo path",
        "Izaberi": "Choose",
        "Osnovna valuta": "Base currency",
        "PDV stopa": "VAT rate",
        "Kurs EUR/BGN": "EUR/BGN rate",
        "Rok plaćanja (dani)": "Payment terms (days)",
        "Način plaćanja": "Payment method",
        "Mesto izdavanja": "Place of issue",
        "Jezik programa": "Application language",
        "Registracija / profil firme": "Company registration / profile",
        "Sačuvaj": "Save",
        "Učitaj iz template-a": "Load from template",
        "SMTP server": "SMTP server",
        "Bezbednost": "Security",
        "Korisnik": "Username",
        "Lozinka": "Password",
        "Pošiljalac ime": "Sender name",
        "Pošiljalac e-mail": "Sender e-mail",
        "Test SMTP": "Test SMTP",
        "Fakturisano ovog meseca": "Invoiced this month",
        "Naplaćeno ovog meseca": "Collected this month",
        "Preostalo za naplatu": "Outstanding balance",
        "Dospelo": "Overdue",
        "Dashboard projekta": "Project dashboard",
        "Fakturisano sa PDV-om": "Invoiced incl. VAT",
        "Otvoreno za naplatu": "Outstanding to collect",
        "Broj izdatih faktura": "Issued invoices",
        "Promet bez PDV-a": "Turnover excl. VAT",
        "Obračunati PDV": "Calculated VAT",
        "Najveći dužnici": "Top debtors",
        "Poslednje uplate": "Latest payments",
        "Kupac": "Customer",
        "Dug": "Balance due",
        "Broj faktura": "Invoices",
        "Najstariji rok": "Oldest due date",
        "Datum": "Date",
        "Broj fakture": "Invoice number",
        "Iznos": "Amount",
        "Pretraga": "Search",
        "Traži": "Search",
        "Novi": "New",
        "Nalepi kupca": "Paste customer",
        "Sačuvaj i novi": "Save and new",
        "Obriši": "Archive",
        "Novi projekat": "New project",
        "Nalepi projekat": "Paste project",
        "Finansije projekta": "Project finances",
        "Otvori projekat": "Open project",
        "Knjigovodstvo projekta": "Project accounting",
        "Nova faktura za projekat": "New project invoice",
        "Dokumenti": "Documents",
        "Dokumenti projekta": "Project documents",
        "Kupac (opciono)": "Customer (optional)",
        "Dodaj kupca": "Add customer",
        "Prihod bez PDV-a": "Revenue excl. VAT",
        "Trošak bez PDV-a": "Cost excl. VAT",
        "Izlazni PDV": "Output VAT",
        "Ulazni PDV": "Input VAT",
        "PDV za uplatu": "VAT payable",
        "Grupa prihoda": "Revenue group",
        "Zarada": "Profit",
        "Gradilište": "Site",
        "Ugovor": "Contract",
        "Kategorija": "Category",
        "Opis": "Description",
        "Količina": "Quantity",
        "Cena": "Price",
        "Ukupno": "Total",
        "Izmeni": "Edit",
        "Nalepi": "Paste",
        "Očisti": "Clear",
        "Otkaži": "Cancel",
        "Dodaj": "Add",
        "Prilozi": "Attachments",
        "Uplate": "Payments",
        "Detalji": "Details",
        "Stavke": "Items",
        "Pregled Excel": "Excel preview",
        "Pregled PDF / štampa": "PDF preview / print",
        "Postavi firmu": "Set up company",
        "Registracija firme": "Company registration",
        "Sačuvaj profil": "Save profile",
        "Kasnije": "Later",
        "Podaci iz ovog profila automatski se koriste na svakoj novoj fakturi.": "Details from this profile are automatically used on every new invoice.",
        "Pristup firmi": "Company access",
        "Prijavite se da otvorite fakture, kupce, projekte i naplate.": "Sign in to open invoices, customers, projects and payments.",
        "Prvo registrujte firmu i postavite e-mail i PIN za lokalnu prijavu.": "First register the company and set an e-mail and PIN for local sign-in.",
        "Registruj firmu": "Register company",
        "Prijavi se": "Sign in",
        "Lokalni pristup štiti ovaj računar. Za rad više firmi preko interneta kasnije se dodaje centralni nalog.": "Local access protects this computer. A central account can later be added for multiple companies online.",
        "Pristup aplikaciji": "Application access",
        "E-mail za prijavu": "Sign-in e-mail",
        "PIN (najmanje 4 cifre)": "PIN (at least 4 digits)",
        "Ponovite PIN": "Repeat PIN",
        "PIN se čuva kao hash i koristi se samo za lokalni pristup ovom računaru.": "The PIN is stored as a hash and is used only for local access on this computer.",
        "Prijava": "Sign in",
        "Prijavite se da otvorite poslovne podatke ove firme.": "Sign in to open this company's business data.",
        "PIN": "PIN",
    },
    "bg": {
        "Fakture, kupci, projekti i naplate": "Фактури, клиенти, проекти и плащания",
        "Šablon fakture": "Шаблон на фактура",
        "Nova faktura": "Нова фактура",
        "Osveži": "Обнови",
        "Smanji": "Минимизирай",
        "Uvećaj": "Увеличи",
        "Vrati": "Възстанови",
        "Dashboard": "Табло",
        "Fakture": "Фактури",
        "Kupci": "Клиенти",
        "Projekti": "Проекти",
        "Firma": "Фирма",
        "Firma i projekti": "Фирма и проекти",
        "Backup": "Архив",
        "Podaci firme": "Данни за фирмата",
        "Dopuni podatke firme": "Допълни данните за фирмата",
        "Podešavanja fakture": "Настройки на фактура",
        "Slanje e-mailom (SMTP)": "Изпращане по e-mail (SMTP)",
        "Naziv": "Име",
        "EIK / BULSTAT": "ЕИК / БУЛСТАТ",
        "PDV broj": "ДДС номер",
        "Adresa": "Адрес",
        "Telefon": "Телефон",
        "E-mail": "E-mail",
        "Banka": "Банка",
        "BIC / SWIFT": "BIC / SWIFT",
        "Direktor": "Управител",
        "Logo putanja": "Път до лого",
        "Izaberi": "Избери",
        "Osnovna valuta": "Основна валута",
        "PDV stopa": "ДДС ставка",
        "Kurs EUR/BGN": "Курс EUR/BGN",
        "Rok plaćanja (dani)": "Срок за плащане (дни)",
        "Način plaćanja": "Начин на плащане",
        "Mesto izdavanja": "Място на издаване",
        "Jezik programa": "Език на програмата",
        "Registracija / profil firme": "Регистрация / профил на фирма",
        "Sačuvaj": "Запази",
        "Učitaj iz template-a": "Зареди от шаблона",
        "SMTP server": "SMTP сървър",
        "Bezbednost": "Сигурност",
        "Korisnik": "Потребител",
        "Lozinka": "Парола",
        "Pošiljalac ime": "Име на подател",
        "Pošiljalac e-mail": "E-mail на подател",
        "Test SMTP": "Тест SMTP",
        "Fakturisano ovog meseca": "Фактурирано този месец",
        "Naplaćeno ovog meseca": "Получено този месец",
        "Preostalo za naplatu": "Остава за плащане",
        "Dospelo": "Просрочено",
        "Dashboard projekta": "Табло на проекта",
        "Fakturisano sa PDV-om": "Фактурирано с ДДС",
        "Otvoreno za naplatu": "Открито за събиране",
        "Broj izdatih faktura": "Издадени фактури",
        "Promet bez PDV-a": "Оборот без ДДС",
        "Obračunati PDV": "Начислен ДДС",
        "Najveći dužnici": "Най-големи длъжници",
        "Poslednje uplate": "Последни плащания",
        "Kupac": "Клиент",
        "Dug": "Задължение",
        "Broj faktura": "Брой фактури",
        "Najstariji rok": "Най-ранен падеж",
        "Datum": "Дата",
        "Broj fakture": "Номер на фактура",
        "Iznos": "Сума",
        "Pretraga": "Търсене",
        "Traži": "Търси",
        "Novi": "Нов",
        "Nalepi kupca": "Постави клиент",
        "Sačuvaj i novi": "Запази и нов",
        "Obriši": "Архивирай",
        "Novi projekat": "Нов проект",
        "Nalepi projekat": "Постави проект",
        "Finansije projekta": "Финанси на проекта",
        "Otvori projekat": "Отвори проект",
        "Knjigovodstvo projekta": "Счетоводство на проекта",
        "Nova faktura za projekat": "Нова фактура за проекта",
        "Dokumenti": "Документи",
        "Dokumenti projekta": "Документи на проекта",
        "Kupac (opciono)": "Клиент (по избор)",
        "Dodaj kupca": "Добави клиент",
        "Prihod bez PDV-a": "Приход без ДДС",
        "Trošak bez PDV-a": "Разход без ДДС",
        "Izlazni PDV": "Изходящ ДДС",
        "Ulazni PDV": "Входящ ДДС",
        "PDV za uplatu": "ДДС за плащане",
        "Grupa prihoda": "Група приходи",
        "Zarada": "Печалба",
        "Gradilište": "Обект",
        "Ugovor": "Договор",
        "Kategorija": "Категория",
        "Opis": "Описание",
        "Količina": "Количество",
        "Cena": "Цена",
        "Ukupno": "Общо",
        "Izmeni": "Редактирай",
        "Nalepi": "Постави",
        "Očisti": "Изчисти",
        "Otkaži": "Отказ",
        "Dodaj": "Добави",
        "Prilozi": "Приложения",
        "Uplate": "Плащания",
        "Detalji": "Детайли",
        "Stavke": "Позиции",
        "Pregled Excel": "Преглед в Excel",
        "Pregled PDF / štampa": "PDF преглед / печат",
        "Postavi firmu": "Настрой фирма",
        "Registracija firme": "Регистрация на фирма",
        "Sačuvaj profil": "Запази профила",
        "Kasnije": "По-късно",
        "Podaci iz ovog profila automatski se koriste na svakoj novoj fakturi.": "Данните от този профил се използват автоматично във всяка нова фактура.",
        "Pristup firmi": "Достъп до фирмата",
        "Prijavite se da otvorite fakture, kupce, projekte i naplate.": "Влезте, за да отворите фактури, клиенти, проекти и плащания.",
        "Prvo registrujte firmu i postavite e-mail i PIN za lokalnu prijavu.": "Първо регистрирайте фирмата и задайте e-mail и PIN за локален вход.",
        "Registruj firmu": "Регистрирай фирма",
        "Prijavi se": "Вход",
        "Lokalni pristup štiti ovaj računar. Za rad više firmi preko interneta kasnije se dodaje centralni nalog.": "Локалният достъп защитава този компютър. За работа на няколко фирми онлайн по-късно се добавя централен профил.",
        "Pristup aplikaciji": "Достъп до приложението",
        "E-mail za prijavu": "E-mail за вход",
        "PIN (najmanje 4 cifre)": "PIN (поне 4 цифри)",
        "Ponovite PIN": "Повторете PIN",
        "PIN se čuva kao hash i koristi se samo za lokalni pristup ovom računaru.": "PIN се пази като hash и се използва само за локален достъп на този компютър.",
        "Prijava": "Вход",
        "Prijavite se da otvorite poslovne podatke ove firme.": "Влезте, за да отворите бизнес данните на тази фирма.",
        "PIN": "PIN",
    },
    "ru": {
        "Fakture, kupci, projekti i naplate": "Счета, клиенты, проекты и платежи",
        "Šablon fakture": "Шаблон счета",
        "Nova faktura": "Новый счет",
        "Osveži": "Обновить",
        "Smanji": "Свернуть",
        "Uvećaj": "Развернуть",
        "Vrati": "Восстановить",
        "Dashboard": "Панель",
        "Fakture": "Счета",
        "Kupci": "Клиенты",
        "Projekti": "Проекты",
        "Firma": "Компания",
        "Firma i projekti": "Компания и проекты",
        "Backup": "Резервная копия",
        "Podaci firme": "Данные компании",
        "Dopuni podatke firme": "Дополнить данные компании",
        "Podešavanja fakture": "Настройки счета",
        "Slanje e-mailom (SMTP)": "Отправка e-mail (SMTP)",
        "Naziv": "Название",
        "EIK / BULSTAT": "ЕИК / БУЛСТАТ",
        "PDV broj": "Номер НДС",
        "Adresa": "Адрес",
        "Telefon": "Телефон",
        "E-mail": "E-mail",
        "Banka": "Банк",
        "BIC / SWIFT": "BIC / SWIFT",
        "Direktor": "Директор",
        "Logo putanja": "Путь к логотипу",
        "Izaberi": "Выбрать",
        "Osnovna valuta": "Основная валюта",
        "PDV stopa": "Ставка НДС",
        "Kurs EUR/BGN": "Курс EUR/BGN",
        "Rok plaćanja (dani)": "Срок оплаты (дни)",
        "Način plaćanja": "Способ оплаты",
        "Mesto izdavanja": "Место выдачи",
        "Jezik programa": "Язык программы",
        "Registracija / profil firme": "Регистрация / профиль компании",
        "Sačuvaj": "Сохранить",
        "Učitaj iz template-a": "Загрузить из шаблона",
        "SMTP server": "SMTP сервер",
        "Bezbednost": "Безопасность",
        "Korisnik": "Пользователь",
        "Lozinka": "Пароль",
        "Pošiljalac ime": "Имя отправителя",
        "Pošiljalac e-mail": "E-mail отправителя",
        "Test SMTP": "Тест SMTP",
        "Fakturisano ovog meseca": "Выставлено в этом месяце",
        "Naplaćeno ovog meseca": "Получено в этом месяце",
        "Preostalo za naplatu": "Остаток к получению",
        "Dospelo": "Просрочено",
        "Dashboard projekta": "Панель проекта",
        "Fakturisano sa PDV-om": "Выставлено с НДС",
        "Otvoreno za naplatu": "Остаток к получению",
        "Broj izdatih faktura": "Выданные счета",
        "Promet bez PDV-a": "Оборот без НДС",
        "Obračunati PDV": "Начисленный НДС",
        "Najveći dužnici": "Крупнейшие должники",
        "Poslednje uplate": "Последние платежи",
        "Kupac": "Клиент",
        "Kupac (opciono)": "Клиент (необязательно)",
        "Dodaj kupca": "Добавить клиента",
        "Dug": "Задолженность",
        "Broj faktura": "Счетов",
        "Najstariji rok": "Самый ранний срок",
        "Datum": "Дата",
        "Broj fakture": "Номер счета",
        "Iznos": "Сумма",
        "Pretraga": "Поиск",
        "Traži": "Найти",
        "Novi": "Новый",
        "Novi projekat": "Новый проект",
        "Nalepi projekat": "Вставить проект",
        "Otvori projekat": "Открыть проект",
        "Knjigovodstvo projekta": "Бухгалтерия проекта",
        "Nova faktura za projekat": "Новый счет для проекта",
        "Finansije projekta": "Финансы проекта",
        "Dokumenti": "Документы",
        "Dokumenti projekta": "Документы проекта",
        "Prihod bez PDV-a": "Доход без НДС",
        "Trošak bez PDV-a": "Расход без НДС",
        "Izlazni PDV": "Исходящий НДС",
        "Ulazni PDV": "Входящий НДС",
        "PDV za uplatu": "НДС к уплате",
        "Grupa prihoda": "Группа доходов",
        "Zarada": "Прибыль",
        "Gradilište": "Строительная площадка",
        "Ugovor": "Договор",
        "Kategorija": "Категория",
        "Opis": "Описание",
        "Količina": "Количество",
        "Cena": "Цена",
        "Ukupno": "Итого",
        "Izmeni": "Изменить",
        "Nalepi": "Вставить",
        "Očisti": "Очистить",
        "Otkaži": "Отмена",
        "Dodaj": "Добавить",
        "Prilozi": "Приложения",
        "Uplate": "Платежи",
        "Detalji": "Детали",
        "Stavke": "Позиции",
        "Pregled Excel": "Просмотр Excel",
        "Pregled PDF / štampa": "Просмотр PDF / печать",
        "Postavi firmu": "Настроить компанию",
        "Registracija firme": "Регистрация компании",
        "Sačuvaj profil": "Сохранить профиль",
        "Kasnije": "Позже",
        "Podaci iz ovog profila automatski se koriste na svakoj novoj fakturi.": "Данные этого профиля автоматически используются в каждом новом счете.",
        "Pristup firmi": "Доступ к компании",
        "Prijavite se da otvorite fakture, kupce, projekte i naplate.": "Войдите, чтобы открыть счета, клиентов, проекты и платежи.",
        "Prvo registrujte firmu i postavite e-mail i PIN za lokalnu prijavu.": "Сначала зарегистрируйте компанию и задайте e-mail и PIN для локального входа.",
        "Registruj firmu": "Зарегистрировать компанию",
        "Prijavi se": "Войти",
        "Lokalni pristup štiti ovaj računar. Za rad više firmi preko interneta kasnije se dodaje centralni nalog.": "Локальный доступ защищает этот компьютер. Центральный аккаунт для нескольких компаний можно добавить позже.",
        "Pristup aplikaciji": "Доступ к приложению",
        "E-mail za prijavu": "E-mail для входа",
        "PIN (najmanje 4 cifre)": "PIN (минимум 4 цифры)",
        "Ponovite PIN": "Повторите PIN",
        "PIN se čuva kao hash i koristi se samo za lokalni pristup ovom računaru.": "PIN хранится как хеш и используется только для локального доступа к этому компьютеру.",
        "Prijava": "Вход",
        "Prijavite se da otvorite poslovne podatke ove firme.": "Войдите, чтобы открыть данные этой компании.",
        "PIN": "PIN",
    },
    "de": {
        "Fakture, kupci, projekti i naplate": "Rechnungen, Kunden, Projekte und Zahlungen",
        "Šablon fakture": "Rechnungsvorlage",
        "Nova faktura": "Neue Rechnung",
        "Osveži": "Aktualisieren",
        "Smanji": "Minimieren",
        "Uvećaj": "Maximieren",
        "Vrati": "Wiederherstellen",
        "Dashboard": "Übersicht",
        "Fakture": "Rechnungen",
        "Kupci": "Kunden",
        "Projekti": "Projekte",
        "Firma": "Firma",
        "Firma i projekti": "Firma und Projekte",
        "Backup": "Sicherung",
        "Podaci firme": "Firmendaten",
        "Dopuni podatke firme": "Firmendaten ergänzen",
        "Podešavanja fakture": "Rechnungseinstellungen",
        "Slanje e-mailom (SMTP)": "E-Mail-Versand (SMTP)",
        "Naziv": "Name",
        "EIK / BULSTAT": "EIK / BULSTAT",
        "PDV broj": "USt-IdNr.",
        "Adresa": "Adresse",
        "Telefon": "Telefon",
        "E-mail": "E-Mail",
        "Banka": "Bank",
        "BIC / SWIFT": "BIC / SWIFT",
        "Direktor": "Geschäftsführer",
        "Logo putanja": "Logo-Pfad",
        "Izaberi": "Auswählen",
        "Osnovna valuta": "Basiswährung",
        "PDV stopa": "USt-Satz",
        "Kurs EUR/BGN": "EUR/BGN-Kurs",
        "Rok plaćanja (dani)": "Zahlungsziel (Tage)",
        "Način plaćanja": "Zahlungsart",
        "Mesto izdavanja": "Ausstellungsort",
        "Jezik programa": "Programmsprache",
        "Registracija / profil firme": "Firmenregistrierung / Profil",
        "Sačuvaj": "Speichern",
        "Učitaj iz template-a": "Aus Vorlage laden",
        "SMTP server": "SMTP-Server",
        "Bezbednost": "Sicherheit",
        "Korisnik": "Benutzer",
        "Lozinka": "Passwort",
        "Pošiljalac ime": "Absendername",
        "Pošiljalac e-mail": "Absender-E-Mail",
        "Test SMTP": "SMTP testen",
        "Preostalo za naplatu": "Offener Betrag",
        "Dospelo": "Überfällig",
        "Dashboard projekta": "Projektübersicht",
        "Fakturisano sa PDV-om": "Berechnet inkl. USt.",
        "Otvoreno za naplatu": "Offen zur Zahlung",
        "Broj izdatih faktura": "Ausgestellte Rechnungen",
        "Promet bez PDV-a": "Umsatz ohne USt.",
        "Obračunati PDV": "Berechnete USt.",
        "Najveći dužnici": "Größte Schuldner",
        "Poslednje uplate": "Letzte Zahlungen",
        "Kupac": "Kunde",
        "Dug": "Offener Betrag",
        "Broj faktura": "Rechnungen",
        "Najstariji rok": "Ältestes Fälligkeitsdatum",
        "Datum": "Datum",
        "Broj fakture": "Rechnungsnummer",
        "Iznos": "Betrag",
        "Pretraga": "Suche",
        "Traži": "Suchen",
        "Novi": "Neu",
        "Nalepi kupca": "Kunden einfügen",
        "Sačuvaj i novi": "Speichern und neu",
        "Obriši": "Archivieren",
        "Novi projekat": "Neues Projekt",
        "Nalepi projekat": "Projekt einfügen",
        "Finansije projekta": "Projektfinanzen",
        "Otvori projekat": "Projekt öffnen",
        "Knjigovodstvo projekta": "Projektbuchhaltung",
        "Nova faktura za projekat": "Neue Projektrechnung",
        "Dokumenti": "Dokumente",
        "Dokumenti projekta": "Projektdokumente",
        "Kupac (opciono)": "Kunde (optional)",
        "Dodaj kupca": "Kunde hinzufügen",
        "Prihod bez PDV-a": "Erlös ohne USt.",
        "Trošak bez PDV-a": "Kosten ohne USt.",
        "Izlazni PDV": "Ausgangs-USt.",
        "Ulazni PDV": "Vorsteuer",
        "PDV za uplatu": "Zahllast USt.",
        "Grupa prihoda": "Erlösgruppe",
        "Zarada": "Gewinn",
        "Gradilište": "Baustelle",
        "Ugovor": "Vertrag",
        "Kategorija": "Kategorie",
        "Opis": "Beschreibung",
        "Količina": "Menge",
        "Cena": "Preis",
        "Ukupno": "Gesamt",
        "Izmeni": "Bearbeiten",
        "Nalepi": "Einfügen",
        "Očisti": "Leeren",
        "Otkaži": "Abbrechen",
        "Dodaj": "Hinzufügen",
        "Prilozi": "Anhänge",
        "Uplate": "Zahlungen",
        "Detalji": "Details",
        "Stavke": "Positionen",
        "Pregled Excel": "Excel-Vorschau",
        "Pregled PDF / štampa": "PDF-Vorschau / Drucken",
        "Postavi firmu": "Firma einrichten",
        "Registracija firme": "Firmenregistrierung",
        "Sačuvaj profil": "Profil speichern",
        "Kasnije": "Später",
        "Pristup firmi": "Firmenzugang",
        "Registruj firmu": "Firma registrieren",
        "Prijavi se": "Anmelden",
        "Pristup aplikaciji": "App-Zugang",
        "E-mail za prijavu": "Anmelde-E-Mail",
        "PIN (najmanje 4 cifre)": "PIN (mindestens 4 Ziffern)",
        "Ponovite PIN": "PIN wiederholen",
        "Prijava": "Anmeldung",
        "Oznaka bloka faktura": "Rechnungsblock-Präfix",
        "Sledeća faktura": "Nächste Rechnung",
        "Uvoz ulaznog PDF računa": "Eingangsrechnung aus PDF importieren",
        "Uvezi podatke iz PDF računa": "Daten aus PDF-Rechnung importieren",
        "Ulazni račun / trošak": "Eingangsrechnung / Kosten",
        "Izlazni račun": "Ausgangsrechnung",
        "Sačuvaj stavku": "Eintrag speichern",
        "Broj računa / dokumenta": "Rechnungs- / Dokumentnummer",
        "Dobavljač / kupac": "Lieferant / Kunde",
        "Iznos bez PDV-a": "Betrag ohne USt.",
        "PDV %": "USt. %",
        "Ukupno sa PDV-om": "Gesamt inkl. USt.",
        "Valuta": "Währung",
        "Napomena": "Notiz",
        "Naziv projekta": "Projektname",
        "Adresa gradilišta": "Baustellenadresse",
        "Broj ugovora": "Vertragsnummer",
        "Broj protokola / Akta 19": "Protokollnummer / Akt 19",
        "Period od (dd.mm.yyyy)": "Zeitraum von (TT.MM.JJJJ)",
        "Period do (dd.mm.yyyy)": "Zeitraum bis (TT.MM.JJJJ)",
        "Poređenja / referenca": "Referenz",
    },
}
EXTENDED_UI_TRANSLATIONS = {
    "Online aktivacija": {"en": "Online activation", "de": "Online-Aktivierung", "bg": "Онлайн активиране", "ru": "Онлайн-активация"},
    "Licenca": {"en": "License", "de": "Lizenz", "bg": "Лиценз", "ru": "Лицензия"},
    "Banka": {"en": "Bank", "de": "Bank", "bg": "Банка", "ru": "Банк"},
    "Dashboard svih projekata": {"en": "All projects dashboard", "de": "Übersicht aller Projekte", "bg": "Табло на всички проекти", "ru": "Панель всех проектов"},
    "Period od": {"en": "Period from", "de": "Zeitraum von", "bg": "Период от", "ru": "Период с"},
    "do": {"en": "to", "de": "bis", "bg": "до", "ru": "по"},
    "Primeni": {"en": "Apply", "de": "Anwenden", "bg": "Приложи", "ru": "Применить"},
    "Ovaj mesec": {"en": "This month", "de": "Dieser Monat", "bg": "Този месец", "ru": "Этот месяц"},
    "Ova godina": {"en": "This year", "de": "Dieses Jahr", "bg": "Тази година", "ru": "Этот год"},
    "Sve vreme": {"en": "All time", "de": "Gesamter Zeitraum", "bg": "За цялото време", "ru": "За всё время"},
    "Otvori sve fakture kupca": {"en": "Open all customer invoices", "de": "Alle Kundenrechnungen öffnen", "bg": "Отвори всички фактури на клиента", "ru": "Открыть все счета клиента"},
    "Otvori fakturu": {"en": "Open invoice", "de": "Rechnung öffnen", "bg": "Отвори фактура", "ru": "Открыть счет"},
    "Uredi fakturu": {"en": "Edit invoice", "de": "Rechnung bearbeiten", "bg": "Редактирай фактура", "ru": "Редактировать счет"},
    "Bankovni izvodi i uplate": {"en": "Bank statements and payments", "de": "Kontoauszüge und Zahlungen", "bg": "Банкови извлечения и плащания", "ru": "Банковские выписки и платежи"},
    "Uvezi izvod": {"en": "Import statement", "de": "Auszug importieren", "bg": "Импортирай извлечение", "ru": "Импортировать выписку"},
    "Potvrdi izabranu uplatu": {"en": "Confirm selected payment", "de": "Ausgewählte Zahlung bestätigen", "bg": "Потвърди избраното плащане", "ru": "Подтвердить выбранный платеж"},
    "Potvrdi sve sigurne": {"en": "Confirm all safe matches", "de": "Alle sicheren Zuordnungen bestätigen", "bg": "Потвърди всички сигурни", "ru": "Подтвердить все надежные"},
    "Ignoriši stavku": {"en": "Ignore item", "de": "Eintrag ignorieren", "bg": "Игнорирай позицията", "ru": "Игнорировать позицию"},
    "Prikaži obrađene": {"en": "Show processed", "de": "Verarbeitete anzeigen", "bg": "Покажи обработените", "ru": "Показать обработанные"},
    "Aktiviraj OpsNest": {"en": "Activate OpsNest", "de": "OpsNest aktivieren", "bg": "Активирай OpsNest", "ru": "Активировать OpsNest"},
    "Uvoz PDF računa": {"en": "PDF invoice import", "de": "PDF-Rechnungsimport", "bg": "Импортиране на PDF фактура", "ru": "Импорт PDF-счета"},
    "Uvoz ulaznog PDF računa": {"en": "Incoming PDF invoice import", "de": "Eingehende PDF-Rechnung importieren", "bg": "Импортиране на входяща PDF фактура", "ru": "Импорт входящего PDF-счета"},
    "Uvoz izlaznog PDF računa": {"en": "Outgoing PDF invoice import", "de": "Ausgehende PDF-Rechnung importieren", "bg": "Импортиране на изходяща PDF фактура", "ru": "Импорт исходящего PDF-счета"},
    "Uvezi ulazni PDF račun": {"en": "Import incoming PDF", "de": "Eingehendes PDF importieren", "bg": "Импортирай входящ PDF", "ru": "Импортировать входящий PDF"},
    "Uvezi izlazni PDF račun": {"en": "Import outgoing PDF", "de": "Ausgehendes PDF importieren", "bg": "Импортирай изходящ PDF", "ru": "Импортировать исходящий PDF"},
    "Izaberite ulazni PDF račun": {"en": "Select incoming PDF invoice", "de": "Eingehende PDF-Rechnung auswählen", "bg": "Изберете входяща PDF фактура", "ru": "Выберите входящий PDF-счет"},
    "Izaberite izlazni PDF račun": {"en": "Select outgoing PDF invoice", "de": "Ausgehende PDF-Rechnung auswählen", "bg": "Изберете изходяща PDF фактура", "ru": "Выберите исходящ PDF-счет"},
    "Naziv firme": {"en": "Company name", "de": "Firmenname", "bg": "Име на фирмата", "ru": "Название компании"},
    "Sve završavate ovde: šaljemo kod na e-mail, a zatim aktiviramo 7 dana besplatnog probnog perioda. Browser se ne otvara.": {"en": "Complete everything here: we send a code by e-mail, then activate a seven-day free trial. No browser opens.", "de": "Alles wird hier erledigt: Wir senden einen Code per E-Mail und aktivieren anschließend die siebentägige kostenlose Testphase. Es wird kein Browser geöffnet.", "bg": "Всичко се прави тук: изпращаме код по e-mail и активираме седемдневен безплатен пробен период. Не се отваря браузър.", "ru": "Всё выполняется здесь: мы отправляем код по e-mail и активируем бесплатный семидневный период. Браузер не открывается."},
    "Unesite poslovni e-mail i kliknite Pošalji kod.": {"en": "Enter your business e-mail and click Send code.", "de": "Geben Sie Ihre geschäftliche E-Mail ein und klicken Sie auf Code senden.", "bg": "Въведете служебния e-mail и натиснете Изпрати код.", "ru": "Введите рабочий e-mail и нажмите Отправить код."},
    "Unesite poslovni e-mail i kod. Ako još nemate kod, kliknite Pošalji kod.": {"en": "Enter your business e-mail and code. If you do not have a code yet, click Send code.", "de": "Geben Sie Ihre geschäftliche E-Mail und den Code ein. Wenn Sie noch keinen Code haben, klicken Sie auf Code senden.", "bg": "Въведете служебния e-mail и кода. Ако все още нямате код, натиснете Изпрати код.", "ru": "Введите рабочий e-mail и код. Если у вас еще нет кода, нажмите Отправить код."},
    "Unesite naziv firme.": {"en": "Enter the company name.", "de": "Geben Sie den Firmennamen ein.", "bg": "Въведете името на фирмата.", "ru": "Введите название компании."},
    "Unesite ispravan poslovni e-mail.": {"en": "Enter a valid business e-mail.", "de": "Geben Sie eine gültige geschäftliche E-Mail ein.", "bg": "Въведете валиден служебен e-mail.", "ru": "Введите корректный рабочий e-mail."},
    "Šaljem verifikacioni kod...": {"en": "Sending verification code...", "de": "Bestätigungscode wird gesendet...", "bg": "Изпращане на код за потвърждение...", "ru": "Отправка кода подтверждения..."},
    "Kod je poslat. Upišite šest cifara iz e-maila i kliknite Potvrdi i aktiviraj.": {"en": "Code sent. Enter the six digits from the e-mail and click Confirm and activate.", "de": "Code gesendet. Geben Sie die sechs Ziffern aus der E-Mail ein und klicken Sie auf Bestätigen und aktivieren.", "bg": "Кодът е изпратен. Въведете шестте цифри от e-mail-а и натиснете Потвърди и активирай.", "ru": "Код отправлен. Введите шесть цифр из e-mail и нажмите Подтвердить и активировать."},
    "Unesite šestocifreni kod sa e-maila.": {"en": "Enter the six-digit code from the e-mail.", "de": "Geben Sie den sechsstelligen Code aus der E-Mail ein.", "bg": "Въведете шестцифрения код от e-mail-а.", "ru": "Введите шестизначный код из e-mail."},
    "Potvrđujem kod i aktiviram probni period...": {"en": "Confirming the code and activating the trial...", "de": "Code wird bestätigt und Testphase aktiviert...", "bg": "Потвърждаване на кода и активиране на пробния период...", "ru": "Подтверждение кода и активация пробного периода..."},
    "Online servis nije vratio potvrdu licence. Pokušajte ponovo.": {"en": "The online service did not return a license confirmation. Try again.", "de": "Der Online-Dienst hat keine Lizenzbestätigung zurückgegeben. Versuchen Sie es erneut.", "bg": "Онлайн услугата не върна потвърждение за лиценз. Опитайте отново.", "ru": "Онлайн-сервис не вернул подтверждение лицензии. Повторите попытку."},
    "Aktivacija završena": {"en": "Activation complete", "de": "Aktivierung abgeschlossen", "bg": "Активирането завърши", "ru": "Активация завершена"},
    "Online licenca je potvrđena. Probni period traje 7 dana bez kartice.": {"en": "Online license confirmed. The trial lasts 7 days with no card required.", "de": "Online-Lizenz bestätigt. Die Testphase dauert 7 Tage ohne Kreditkarte.", "bg": "Онлайн лицензът е потвърден. Пробният период е 7 дни без карта.", "ru": "Онлайн-лиценз подтверждён. Пробный период длится 7 дней без карты."},
    "Poslovni e-mail": {"en": "Business e-mail", "de": "Geschäftliche E-Mail", "bg": "Служебен e-mail", "ru": "Рабочий e-mail"},
    "Kod sa e-maila": {"en": "Code from e-mail", "de": "Code aus der E-Mail", "bg": "Код от e-mail", "ru": "Код из e-mail"},
    "Kod važi 15 minuta. Nakon 5 pogrešnih unosa tražite novi kod.": {"en": "The code is valid for 15 minutes. Request a new code after 5 incorrect entries.", "de": "Der Code ist 15 Minuten gültig. Nach 5 falschen Eingaben fordern Sie einen neuen Code an.", "bg": "Кодът е валиден 15 минути. След 5 грешни опита поискайте нов код.", "ru": "Код действует 15 минут. После 5 неверных попыток запросите новый код."},
    "Pošalji kod": {"en": "Send code", "de": "Code senden", "bg": "Изпрати код", "ru": "Отправить код"},
    "Potvrdi i aktiviraj": {"en": "Confirm and activate", "de": "Bestätigen und aktivieren", "bg": "Потвърди и активирай", "ru": "Подтвердить и активировать"},
    "Zatvori": {"en": "Close", "de": "Schließen", "bg": "Затвори", "ru": "Закрыть"},
    "Glavne akcije": {"en": "Main actions", "de": "Hauptaktionen", "bg": "Основни действия", "ru": "Основные действия"},
    "Dodaj trošak / ulazni račun": {"en": "Add cost / incoming invoice", "de": "Kosten / Eingangsrechnung hinzufügen", "bg": "Добави разход / входяща фактура", "ru": "Добавить расход / входящий счет"},
    "Dodaj uplatu": {"en": "Add payment", "de": "Zahlung hinzufügen", "bg": "Добави плащане", "ru": "Добавить платеж"},
    "Pregled zarade": {"en": "Profit overview", "de": "Gewinnübersicht", "bg": "Преглед на печалбата", "ru": "Обзор прибыли"},
    "Dodaj izlazni račun": {"en": "Add outgoing invoice", "de": "Ausgangsrechnung hinzufügen", "bg": "Добави изходяща фактура", "ru": "Добавить исходящий счет"},
    "Budžet projekta": {"en": "Project budget", "de": "Projektbudget", "bg": "Бюджет на проекта", "ru": "Бюджет проекта"},
    "PDV evidencija": {"en": "VAT register", "de": "USt.-Übersicht", "bg": "ДДС регистър", "ru": "Реестр НДС"},
    "Izvoz za knjigovođu": {"en": "Export for accountant", "de": "Export für Buchhaltung", "bg": "Експорт за счетоводител", "ru": "Экспорт для бухгалтера"},
    "Funkcije izabrane fakture": {"en": "Selected invoice actions", "de": "Funktionen der ausgewählten Rechnung", "bg": "Действия за избраната фактура", "ru": "Действия выбранного счета"},
    "Povraćaj": {"en": "Refund", "de": "Erstattung", "bg": "Възстановяване", "ru": "Возврат"},
    "Izdaj odobrenje": {"en": "Issue credit note", "de": "Gutschrift ausstellen", "bg": "Издай кредитно известие", "ru": "Оформить кредит-ноту"},
    "PDF / štampa": {"en": "PDF / print", "de": "PDF / Drucken", "bg": "PDF / печат", "ru": "PDF / печать"},
    "PDV evidencija projekta": {"en": "Project VAT register", "de": "Projekt-USt.-Übersicht", "bg": "ДДС регистър на проекта", "ru": "Реестр НДС проекта"},
    "Jedan klik za knjigovođu": {"en": "One click for accountant", "de": "Ein Klick für die Buchhaltung", "bg": "Един клик за счетоводителя", "ru": "Один клик для бухгалтера"},
    "Napravi PDF i Excel": {"en": "Create PDF and Excel", "de": "PDF und Excel erstellen", "bg": "Създай PDF и Excel", "ru": "Создать PDF и Excel"},
    "Plan projekta bez PDV-a": {"en": "Project plan excl. VAT", "de": "Projektplan ohne USt.", "bg": "План на проекта без ДДС", "ru": "План проекта без НДС"},
    "Planirani iznosi bez PDV-a": {"en": "Planned amounts excl. VAT", "de": "Geplante Beträge ohne USt.", "bg": "Планирани суми без ДДС", "ru": "Плановые суммы без НДС"},
    "Plan naspram stvarnog": {"en": "Plan versus actual", "de": "Plan gegenüber Ist", "bg": "План спрямо действително", "ru": "План и факт"},
    "Sažetak PDV-a": {"en": "VAT summary", "de": "USt.-Zusammenfassung", "bg": "Обобщение на ДДС", "ru": "Сводка НДС"},
    "Fakture kupca": {"en": "Customer invoices", "de": "Kundenrechnungen", "bg": "Фактури на клиента", "ru": "Счета клиента"},
    "Dodaj dokument": {"en": "Add document", "de": "Dokument hinzufügen", "bg": "Добави документ", "ru": "Добавить документ"},
    "Otvori folder": {"en": "Open folder", "de": "Ordner öffnen", "bg": "Отвори папка", "ru": "Открыть папку"},
    "Početni vodič za projekat": {"en": "Project quick-start guide", "de": "Projekt-Schnellstart", "bg": "Начално ръководство за проекта", "ru": "Начальное руководство по проекту"},
    "Podsetnici projekta": {"en": "Project reminders", "de": "Projekterinnerungen", "bg": "Напомняния за проекта", "ru": "Напоминания проекта"},
    "Izaberite OpsNest paket": {"en": "Choose an OpsNest plan", "de": "OpsNest-Paket auswählen", "bg": "Изберете пакет OpsNest", "ru": "Выберите пакет OpsNest"},
    "Plaćanje se bezbedno završava preko PayPal-a. Pretplatu možete otkazati iz PayPal naloga.": {"en": "Payment is securely completed through PayPal. You can cancel the subscription from your PayPal account.", "de": "Die Zahlung wird sicher über PayPal abgeschlossen. Sie können das Abonnement in Ihrem PayPal-Konto kündigen.", "bg": "Плащането се завършва сигурно чрез PayPal. Можете да отмените абонамента от PayPal профила си.", "ru": "Оплата безопасно завершается через PayPal. Подписку можно отменить в аккаунте PayPal."},
    "mesečna pretplata": {"en": "monthly subscription", "de": "monatliches Abonnement", "bg": "месечен абонамент", "ru": "ежемесячная подписка"},
    "Izaberi Starter": {"en": "Choose Starter", "de": "Starter wählen", "bg": "Избери Starter", "ru": "Выбрать Starter"},
    "Izaberi Business": {"en": "Choose Business", "de": "Business wählen", "bg": "Избери Business", "ru": "Выбрать Business"},
    "Izaberi Pro": {"en": "Choose Pro", "de": "Pro wählen", "bg": "Избери Pro", "ru": "Выбрать Pro"},
}
for _source_text, _translations in EXTENDED_UI_TRANSLATIONS.items():
    for _language_code, _translated_text in _translations.items():
        UI_TRANSLATIONS[_language_code].setdefault(_source_text, _translated_text)

# Keep interface text in one language. These strings are used by the project,
# banking, customer, and dashboard screens, including values filled at runtime.
COMPLETE_UI_TRANSLATIONS = {
    "Fakturisano u periodu": {"en": "Invoiced in period", "de": "Im Zeitraum fakturiert", "bg": "Фактурирано за периода", "ru": "Выставлено за период"},
    "Naplaćeno u periodu": {"en": "Collected in period", "de": "Im Zeitraum eingezogen", "bg": "Събрано за периода", "ru": "Получено за период"},
    "Preostalo za naplatu": {"en": "Outstanding balance", "de": "Offener Saldo", "bg": "Неплатено салдо", "ru": "Остаток к получению"},
    "Dospelo u periodu": {"en": "Overdue in period", "de": "Im Zeitraum überfällig", "bg": "Просрочено за периода", "ru": "Просрочено за период"},
    "Broj izdatih faktura": {"en": "Issued invoices", "de": "Ausgestellte Rechnungen", "bg": "Издадени фактури", "ru": "Выданные счета"},
    "Promet bez PDV-a": {"en": "Turnover excl. VAT", "de": "Umsatz ohne USt.", "bg": "Оборот без ДДС", "ru": "Оборот без НДС"},
    "Obračunati PDV": {"en": "Calculated VAT", "de": "Berechnete USt.", "bg": "Начислен ДДС", "ru": "Начисленный НДС"},
    "Svi projekti zajedno | Period: {from_date} - {to_date}": {"en": "All projects | Period: {from_date} - {to_date}", "de": "Alle Projekte | Zeitraum: {from_date} - {to_date}", "bg": "Всички проекти | Период: {from_date} - {to_date}", "ru": "Все проекты | Период: {from_date} - {to_date}"},
    "Svi projekti zajedno | Period: sve vreme": {"en": "All projects | All time", "de": "Alle Projekte | Gesamter Zeitraum", "bg": "Всички проекти | За цялото време", "ru": "Все проекты | За все время"},
    "početak": {"en": "start", "de": "Beginn", "bg": "начало", "ru": "начало"},
    "danas": {"en": "today", "de": "heute", "bg": "днес", "ru": "сегодня"},
    "Najveći dužnici": {"en": "Top debtors", "de": "Größte Schuldner", "bg": "Най-големи длъжници", "ru": "Крупнейшие должники"},
    "Najstariji rok": {"en": "Oldest due date", "de": "Ältestes Fälligkeitsdatum", "bg": "Най-стар падеж", "ru": "Самый ранний срок"},
    "Dug": {"en": "Balance due", "de": "Offener Betrag", "bg": "Дължима сума", "ru": "Сумма долга"},
    "Izaberite kupca pa otvorite sve njegove fakture.": {"en": "Select a customer to open all of their invoices.", "de": "Wählen Sie einen Kunden, um alle Rechnungen zu öffnen.", "bg": "Изберете клиент, за да отворите всички негови фактури.", "ru": "Выберите клиента, чтобы открыть все его счета."},
    "Otvori sve fakture kupca": {"en": "Open all customer invoices", "de": "Alle Kundenrechnungen öffnen", "bg": "Отвори всички фактури на клиента", "ru": "Открыть все счета клиента"},
    "Poslednje uplate": {"en": "Latest payments", "de": "Letzte Zahlungen", "bg": "Последни плащания", "ru": "Последние платежи"},
    "Dvoklik na uplatu otvara njenu fakturu.": {"en": "Double-click a payment to open its invoice.", "de": "Doppelklick auf eine Zahlung öffnet ihre Rechnung.", "bg": "Двойно щракване върху плащане отваря фактурата му.", "ru": "Двойной щелчок по платежу открывает его счет."},
    "Bankovni izvodi i uplate": {"en": "Bank statements and payments", "de": "Kontoauszüge und Zahlungen", "bg": "Банкови извлечения и плащания", "ru": "Банковские выписки и платежи"},
    "Uvezite CSV ili XLSX izvod. OpsNest samo predlaže fakturu, a uplata se knjiži tek nakon vaše potvrde.": {"en": "Import a CSV or XLSX statement. OpsNest only suggests an invoice; a payment is posted after your confirmation.", "de": "Importieren Sie einen CSV- oder XLSX-Auszug. OpsNest schlägt nur eine Rechnung vor; die Zahlung wird erst nach Ihrer Bestätigung gebucht.", "bg": "Импортирайте CSV или XLSX извлечение. OpsNest само предлага фактура; плащането се осчетоводява след потвърждение.", "ru": "Импортируйте выписку CSV или XLSX. OpsNest только предлагает счет; платеж проводится после вашего подтверждения."},
    "Uvezi izvod": {"en": "Import statement", "de": "Auszug importieren", "bg": "Импортирай извлечение", "ru": "Импортировать выписку"},
    "Potvrdi izabranu uplatu": {"en": "Confirm selected payment", "de": "Ausgewählte Zahlung bestätigen", "bg": "Потвърди избраното плащане", "ru": "Подтвердить выбранный платеж"},
    "Potvrdi sve sigurne": {"en": "Confirm all safe matches", "de": "Alle sicheren Treffer bestätigen", "bg": "Потвърди всички сигурни съвпадения", "ru": "Подтвердить все надежные совпадения"},
    "Ignoriši stavku": {"en": "Ignore item", "de": "Eintrag ignorieren", "bg": "Игнорирай реда", "ru": "Игнорировать запись"},
    "Prikaži obrađene": {"en": "Show processed", "de": "Verarbeitete anzeigen", "bg": "Покажи обработените", "ru": "Показать обработанные"},
    "Za proveru": {"en": "To review", "de": "Zu prüfen", "bg": "За проверка", "ru": "На проверку"},
    "Sa predlogom": {"en": "With suggestion", "de": "Mit Vorschlag", "bg": "С предложение", "ru": "С предложением"},
    "Potvrđene uplate": {"en": "Confirmed payments", "de": "Bestätigte Zahlungen", "bg": "Потвърдени плащания", "ru": "Подтвержденные платежи"},
    "Uplatilac": {"en": "Payer", "de": "Zahler", "bg": "Платец", "ru": "Плательщик"},
    "Predlog fakture": {"en": "Invoice suggestion", "de": "Rechnungsvorschlag", "bg": "Предложена фактура", "ru": "Предложение счета"},
    "Osnov predloga": {"en": "Match basis", "de": "Grund des Vorschlags", "bg": "Основание за предложението", "ru": "Основание предложения"},
    "Sigurnost": {"en": "Confidence", "de": "Sicherheit", "bg": "Надеждност", "ru": "Надежность"},
    "Bez predloga": {"en": "No suggestion", "de": "Kein Vorschlag", "bg": "Без предложение", "ru": "Без предложения"},
    "Predlog": {"en": "Suggested", "de": "Vorgeschlagen", "bg": "Предложено", "ru": "Предложено"},
    "Potvrđena": {"en": "Confirmed", "de": "Bestätigt", "bg": "Потвърдено", "ru": "Подтверждено"},
    "Ignorisana": {"en": "Ignored", "de": "Ignoriert", "bg": "Игнорирано", "ru": "Игнорировано"},
    "Ručni unos kupca: sačuvani podaci se zatim automatski prepisuju na novu fakturu.": {"en": "Manual customer entry: saved details are copied automatically to a new invoice.", "de": "Manuelle Kundeneingabe: gespeicherte Daten werden automatisch in eine neue Rechnung übernommen.", "bg": "Ръчно въвеждане на клиент: записаните данни се копират автоматично в нова фактура.", "ru": "Ручной ввод клиента: сохраненные данные автоматически переносятся в новый счет."},
    "Odgovorno lice": {"en": "Contact person", "de": "Ansprechpartner", "bg": "Лице за контакт", "ru": "Контактное лицо"},
    "Napomena": {"en": "Note", "de": "Notiz", "bg": "Бележка", "ru": "Примечание"},
    "Rok": {"en": "Term", "de": "Frist", "bg": "Срок", "ru": "Срок"},
    "Projekat je glavna jedinica rada. Kupac je opcioni podatak projekta; na svakoj fakturi birate konkretnog kupca.": {"en": "A project is the main unit of work. A customer is optional on the project; select the specific customer on each invoice.", "de": "Ein Projekt ist die zentrale Arbeitseinheit. Ein Kunde ist beim Projekt optional; wählen Sie den konkreten Kunden auf jeder Rechnung.", "bg": "Проектът е основната работна единица. Клиентът е незадължителен в проекта; изберете конкретния клиент за всяка фактура.", "ru": "Проект является основной единицей работы. Клиент в проекте необязателен; выбирайте конкретного клиента для каждого счета."},
    "Kupac (opciono)": {"en": "Customer (optional)", "de": "Kunde (optional)", "bg": "Клиент (по избор)", "ru": "Клиент (необязательно)"},
    "Naziv projekta": {"en": "Project name", "de": "Projektname", "bg": "Име на проекта", "ru": "Название проекта"},
    "Oznaka bloka faktura": {"en": "Invoice block prefix", "de": "Rechnungsblock-Präfix", "bg": "Префикс на блока фактури", "ru": "Префикс блока счетов"},
    "Ako ostavite prazno, program dodeljuje prvi slobodan blok. Primer: 1 -> 1000000001.": {"en": "If left empty, the app assigns the first available block. Example: 1 -> 1000000001.", "de": "Wenn leer, weist die App den ersten freien Block zu. Beispiel: 1 -> 1000000001.", "bg": "Ако оставите празно, програмата задава първия свободен блок. Пример: 1 -> 1000000001.", "ru": "Если оставить пустым, программа назначит первый свободный блок. Пример: 1 -> 1000000001."},
    "Adresa gradilišta": {"en": "Site address", "de": "Baustellenadresse", "bg": "Адрес на обекта", "ru": "Адрес строительного объекта"},
    "Broj ugovora": {"en": "Contract number", "de": "Vertragsnummer", "bg": "Номер на договор", "ru": "Номер договора"},
    "Broj protokola / Akta 19": {"en": "Protocol / Act 19 number", "de": "Protokoll-/Akt-19-Nummer", "bg": "Номер на протокол / Акт 19", "ru": "Номер протокола / Акта 19"},
    "Period od (dd.mm.yyyy)": {"en": "Period from (dd.mm.yyyy)", "de": "Zeitraum von (TT.MM.JJJJ)", "bg": "Период от (дд.мм.гггг)", "ru": "Период с (дд.мм.гггг)"},
    "Period do (dd.mm.yyyy)": {"en": "Period to (dd.mm.yyyy)", "de": "Zeitraum bis (TT.MM.JJJJ)", "bg": "Период до (дд.мм.гггг)", "ru": "Период по (дд.мм.гггг)"},
    "Poređenja / referenca": {"en": "Comparison / reference", "de": "Vergleich / Referenz", "bg": "Сравнение / референция", "ru": "Сравнение / ссылка"},
    "Projektni račun / trošak": {"en": "Project invoice / cost", "de": "Projektrechnung / Kosten", "bg": "Проектна фактура / разход", "ru": "Счет проекта / расход"},
    "Ulazni i izlazni račun projekta": {"en": "Project input and output document", "de": "Projekt-Eingangs- und Ausgangsdokument", "bg": "Входящ и изходящ документ на проекта", "ru": "Входящий и исходящий документ проекта"},
    "Uvoz PDF računa": {"en": "Import invoice PDF", "de": "Rechnungs-PDF importieren", "bg": "Импорт на PDF фактура", "ru": "Импорт PDF счета"},
    "Uvezi podatke iz PDF računa": {"en": "Import data from invoice PDF", "de": "Daten aus Rechnungs-PDF importieren", "bg": "Импортирай данни от PDF фактура", "ru": "Импортировать данные из PDF счета"},
    "PDF račun još nije izabran.": {"en": "No invoice PDF selected yet.", "de": "Noch kein Rechnungs-PDF ausgewählt.", "bg": "Все още не е избрана PDF фактура.", "ru": "PDF счета еще не выбран."},
    "Pre čuvanja proverite prepoznate podatke. PDF se kopira u projekat.": {"en": "Review the recognized data before saving. The PDF is copied to the project.", "de": "Prüfen Sie die erkannten Daten vor dem Speichern. Das PDF wird in das Projekt kopiert.", "bg": "Проверете разпознатите данни преди запис. PDF файлът се копира в проекта.", "ru": "Проверьте распознанные данные перед сохранением. PDF копируется в проект."},
    "Ulazni račun / trošak": {"en": "Input invoice / cost", "de": "Eingangsrechnung / Kosten", "bg": "Входяща фактура / разход", "ru": "Входящий счет / расход"},
    "Izlazni račun": {"en": "Output invoice", "de": "Ausgangsrechnung", "bg": "Изходяща фактура", "ru": "Исходящий счет"},
    "Grupa troška": {"en": "Cost group", "de": "Kostenart", "bg": "Група разходи", "ru": "Группа расходов"},
    "Grupa prihoda": {"en": "Revenue group", "de": "Erlösgruppe", "bg": "Група приходи", "ru": "Группа доходов"},
    "Broj računa / dokumenta": {"en": "Invoice / document number", "de": "Rechnungs-/Dokumentnummer", "bg": "Номер на фактура / документ", "ru": "Номер счета / документа"},
    "Dobavljač / kupac": {"en": "Supplier / customer", "de": "Lieferant / Kunde", "bg": "Доставчик / клиент", "ru": "Поставщик / клиент"},
    "Iznos bez PDV-a": {"en": "Amount excl. VAT", "de": "Betrag ohne USt.", "bg": "Сума без ДДС", "ru": "Сумма без НДС"},
    "Ukupno sa PDV-om": {"en": "Total incl. VAT", "de": "Gesamt mit USt.", "bg": "Общо с ДДС", "ru": "Итого с НДС"},
    "Sačuvaj stavku": {"en": "Save item", "de": "Eintrag speichern", "bg": "Запази реда", "ru": "Сохранить запись"},
    "Radovi": {"en": "Works", "de": "Arbeiten", "bg": "Работи", "ru": "Работы"},
    "Ostali prihodi": {"en": "Other revenue", "de": "Sonstige Erlöse", "bg": "Други приходи", "ru": "Прочие доходы"},
    "Dashboard i knjigovodstvo projekta": {"en": "Project dashboard and accounting", "de": "Projekt-Dashboard und Buchhaltung", "bg": "Табло и счетоводство на проекта", "ru": "Панель и учет проекта"},
    "Dashboard projekta": {"en": "Project dashboard", "de": "Projekt-Dashboard", "bg": "Табло на проекта", "ru": "Панель проекта"},
    "Ulazni račun = vaš trošak | Izlazna faktura = vaš prihod | PDV za uplatu = izlazni PDV - ulazni PDV": {"en": "Input invoice = your cost | Output invoice = your revenue | VAT payable = output VAT - input VAT", "de": "Eingangsrechnung = Ihre Kosten | Ausgangsrechnung = Ihre Einnahmen | Zahllast = Ausgangs-USt. - Eingangs-USt.", "bg": "Входяща фактура = ваш разход | Изходяща фактура = ваш приход | ДДС за плащане = изходящ ДДС - входящ ДДС", "ru": "Входящий счет = ваши расходы | Исходящий счет = ваши доходы | НДС к уплате = исходящий НДС - входящий НДС"},
    "Početni vodič: {completed}/5 koraka završeno": {"en": "Quick start: {completed}/5 steps completed", "de": "Schnellstart: {completed}/5 Schritte abgeschlossen", "bg": "Начално ръководство: {completed}/5 стъпки завършени", "ru": "Быстрый старт: выполнено {completed}/5 шагов"},
    "Podsetnici: ": {"en": "Reminders: ", "de": "Erinnerungen: ", "bg": "Напомняния: ", "ru": "Напоминания: "},
    "rokovi u 7 dana: {count}": {"en": "due within 7 days: {count}", "de": "fällig innerhalb von 7 Tagen: {count}", "bg": "срок до 7 дни: {count}", "ru": "срок в течение 7 дней: {count}"},
    "dospeli kupci: {count}": {"en": "overdue customers: {count}", "de": "überfällige Kunden: {count}", "bg": "просрочени клиенти: {count}", "ru": "просроченные клиенты: {count}"},
    "ulazni računi bez PDF-a: {count}": {"en": "input invoices without PDF: {count}", "de": "Eingangsrechnungen ohne PDF: {count}", "bg": "входящи фактури без PDF: {count}", "ru": "входящие счета без PDF: {count}"},
    "budžet prekoračen: {count}": {"en": "budget exceeded: {count}", "de": "Budget überschritten: {count}", "bg": "бюджетът е надвишен: {count}", "ru": "бюджет превышен: {count}"},
    "nema otvorenih obaveza": {"en": "no open reminders", "de": "keine offenen Erinnerungen", "bg": "няма отворени напомняния", "ru": "нет открытых напоминаний"},
    "Glavne akcije": {"en": "Main actions", "de": "Hauptaktionen", "bg": "Основни действия", "ru": "Основные действия"},
    "Dodaj trošak / ulazni račun": {"en": "Add cost / input invoice", "de": "Kosten / Eingangsrechnung hinzufügen", "bg": "Добави разход / входяща фактура", "ru": "Добавить расход / входящий счет"},
    "Pregled zarade": {"en": "Profit overview", "de": "Gewinnübersicht", "bg": "Преглед на печалбата", "ru": "Обзор прибыли"},
    "Dodaj izlazni račun": {"en": "Add output invoice", "de": "Ausgangsrechnung hinzufügen", "bg": "Добави изходяща фактура", "ru": "Добавить исходящий счет"},
    "Budžet projekta": {"en": "Project budget", "de": "Projektbudget", "bg": "Бюджет на проекта", "ru": "Бюджет проекта"},
    "PDV evidencija": {"en": "VAT register", "de": "USt.-Register", "bg": "ДДС регистър", "ru": "Реестр НДС"},
    "Izvoz za knjigovođu": {"en": "Export for accountant", "de": "Export für Buchhalter", "bg": "Експорт за счетоводител", "ru": "Экспорт для бухгалтера"},
    "Dokumenti projekta": {"en": "Project documents", "de": "Projektdokumente", "bg": "Документи на проекта", "ru": "Документы проекта"},
    "Funkcije izabrane fakture": {"en": "Selected invoice actions", "de": "Aktionen für ausgewählte Rechnung", "bg": "Действия за избраната фактура", "ru": "Действия выбранного счета"},
    "Prilozi fakture": {"en": "Invoice attachments", "de": "Rechnungsanhänge", "bg": "Приложения към фактурата", "ru": "Вложения счета"},
    "Prihod bez PDV-a": {"en": "Revenue excl. VAT", "de": "Erlös ohne USt.", "bg": "Приход без ДДС", "ru": "Доход без НДС"},
    "Fakturisano sa PDV-om": {"en": "Invoiced incl. VAT", "de": "Fakturiert mit USt.", "bg": "Фактурирано с ДДС", "ru": "Выставлено с НДС"},
    "Otvoreno za naplatu": {"en": "Open for collection", "de": "Offen zur Zahlung", "bg": "Отворено за събиране", "ru": "Открыто к получению"},
    "Izlazni PDV": {"en": "Output VAT", "de": "Ausgangs-USt.", "bg": "Изходящ ДДС", "ru": "Исходящий НДС"},
    "Izdata odobrenja": {"en": "Issued credit notes", "de": "Ausgestellte Gutschriften", "bg": "Издадени кредитни известия", "ru": "Выданные кредит-ноты"},
    "Ulazni PDV": {"en": "Input VAT", "de": "Eingangs-USt.", "bg": "Входящ ДДС", "ru": "Входящий НДС"},
    "PDV za uplatu": {"en": "VAT payable", "de": "Zahllast", "bg": "ДДС за плащане", "ru": "НДС к уплате"},
    "Ukupan trošak": {"en": "Total cost", "de": "Gesamtkosten", "bg": "Общ разход", "ru": "Общие расходы"},
    "Zarada bez PDV-a": {"en": "Profit excl. VAT", "de": "Gewinn ohne USt.", "bg": "Печалба без ДДС", "ru": "Прибыль без НДС"},
    "Planiran prihod": {"en": "Planned revenue", "de": "Geplanter Erlös", "bg": "Планиран приход", "ru": "Планируемый доход"},
    "Planiran trošak": {"en": "Planned cost", "de": "Geplante Kosten", "bg": "Планиран разход", "ru": "Планируемые расходы"},
    "Planirana zarada": {"en": "Planned profit", "de": "Geplanter Gewinn", "bg": "Планирана печалба", "ru": "Планируемая прибыль"},
    "Odstupanje zarade": {"en": "Profit variance", "de": "Gewinnabweichung", "bg": "Отклонение на печалбата", "ru": "Отклонение прибыли"},
    "Budžet nije unet": {"en": "Budget not entered", "de": "Budget nicht eingegeben", "bg": "Бюджетът не е въведен", "ru": "Бюджет не введен"},
    "Izdana faktura": {"en": "Issued invoice", "de": "Ausgestellte Rechnung", "bg": "Издадена фактура", "ru": "Выданный счет"},
    "Prihod projekta": {"en": "Project revenue", "de": "Projekterlös", "bg": "Приход от проекта", "ru": "Доход проекта"},
    "Kreditno odobrenje": {"en": "Credit note", "de": "Gutschrift", "bg": "Кредитно известие", "ru": "Кредит-нота"},
    "Umanjenje prihoda": {"en": "Revenue reduction", "de": "Erlösminderung", "bg": "Намаление на приход", "ru": "Уменьшение дохода"},
    "Uz fakturu {invoice}: {reason}": {"en": "For invoice {invoice}: {reason}", "de": "Zu Rechnung {invoice}: {reason}", "bg": "Към фактура {invoice}: {reason}", "ru": "К счету {invoice}: {reason}"},
    "Faktura": {"en": "Invoice", "de": "Rechnung", "bg": "Фактура", "ru": "Счет"},
    "Sledeća faktura": {"en": "Next invoice", "de": "Nächste Rechnung", "bg": "Следваща фактура", "ru": "Следующий счет"},
    "Sledeća faktura ovog projekta biće: {number}. Prefiks se zaključava nakon prve fakture.": {"en": "The next invoice for this project will be: {number}. The prefix is locked after the first invoice.", "de": "Die nächste Rechnung dieses Projekts lautet: {number}. Das Präfix wird nach der ersten Rechnung gesperrt.", "bg": "Следващата фактура за този проект ще бъде: {number}. Префиксът се заключва след първата фактура.", "ru": "Следующий счет этого проекта будет: {number}. Префикс блокируется после первого счета."},
    "Dopunite podatke firme": {"en": "Complete company details", "de": "Unternehmensdaten ergänzen", "bg": "Допълнете данните за фирмата", "ru": "Заполните данные компании"},
    "PDV stopa": {"en": "VAT rate", "de": "USt.-Satz", "bg": "Ставка ДДС", "ru": "Ставка НДС"},
    "Rok plaćanja": {"en": "Payment terms", "de": "Zahlungsfrist", "bg": "Срок за плащане", "ru": "Срок оплаты"},
    "dana": {"en": "days", "de": "Tage", "bg": "дни", "ru": "дней"},
    "Otvori vodič": {"en": "Open guide", "de": "Leitfaden öffnen", "bg": "Отвори ръководството", "ru": "Открыть руководство"},
    "Podsetnici": {"en": "Reminders", "de": "Erinnerungen", "bg": "Напомняния", "ru": "Напоминания"},
    "Faktura projekta": {"en": "Project invoice", "de": "Projektrechnung", "bg": "Фактура на проекта", "ru": "Счет проекта"},
    "Prepoznato ({method}): {filename}. Proverite podatke pre čuvanja.{partner_status}": {"en": "Recognized ({method}): {filename}. Review the data before saving.{partner_status}", "de": "Erkannt ({method}): {filename}. Prüfen Sie die Daten vor dem Speichern.{partner_status}", "bg": "Разпознато ({method}): {filename}. Проверете данните преди запис.{partner_status}", "ru": "Распознано ({method}): {filename}. Проверьте данные перед сохранением.{partner_status}"},
    " Partner je povezan sa bazom firmi.": {"en": " The partner is matched with the company database.", "de": " Der Partner wurde mit der Unternehmensdatenbank abgeglichen.", "bg": " Партньорът е свързан с базата данни на фирмите.", "ru": " Партнер сопоставлен с базой компаний."},
}
for _source_text, _translations in COMPLETE_UI_TRANSLATIONS.items():
    for _language_code, _translated_text in _translations.items():
        UI_TRANSLATIONS[_language_code][_source_text] = _translated_text


# Final coverage for labels and messages created inside secondary windows.
# Keeping these here means every language switch uses one authoritative glossary.
FINAL_UI_TRANSLATIONS = {
    "Projekat": {"en": "Project", "de": "Projekt", "bg": "Проект", "ru": "Проект"},
    "PDV": {"en": "VAT", "de": "USt.", "bg": "ДДС", "ru": "НДС"},
    "Valuta": {"en": "Currency", "de": "Währung", "bg": "Валута", "ru": "Валюта"},
    "Referenca": {"en": "Reference", "de": "Referenz", "bg": "Референция", "ru": "Ссылка"},
    "Lice": {"en": "Contact", "de": "Ansprechpartner", "bg": "Лице за контакт", "ru": "Контактное лицо"},
    "Period": {"en": "Period", "de": "Zeitraum", "bg": "Период", "ru": "Период"},
    "Tip": {"en": "Type", "de": "Typ", "bg": "Тип", "ru": "Тип"},
    "Broj": {"en": "Number", "de": "Nummer", "bg": "Номер", "ru": "Номер"},
    "Otvori": {"en": "Open", "de": "Öffnen", "bg": "Отвори", "ru": "Открыть"},
    "Excel šablon": {"en": "Excel template", "de": "Excel-Vorlage", "bg": "Excel шаблон", "ru": "Шаблон Excel"},
    "Izaberite bankovni izvod": {"en": "Select bank statement", "de": "Kontoauszug auswählen", "bg": "Изберете банково извлечение", "ru": "Выберите банковскую выписку"},
    "Izvod nije moguće uvesti:": {"en": "The statement could not be imported:", "de": "Der Kontoauszug konnte nicht importiert werden:", "bg": "Извлечението не може да бъде импортирано:", "ru": "Не удалось импортировать выписку:"},
    "Bankovni izvod": {"en": "Bank statement", "de": "Kontoauszug", "bg": "Банково извлечение", "ru": "Банковская выписка"},
    "Uplate sa manjom sigurnošću ostaće za ručnu proveru.": {"en": "Lower-confidence payments remain for manual review.", "de": "Zahlungen mit niedriger Sicherheit bleiben zur manuellen Prüfung.", "bg": "Плащанията с по-ниска сигурност остават за ръчна проверка.", "ru": "Платежи с низкой уверенностью останутся для ручной проверки."},
    "Potvrdi uplatu sa bankovnog izvoda": {"en": "Confirm payment from bank statement", "de": "Zahlung aus Kontoauszug bestätigen", "bg": "Потвърдете плащане от банковото извлечение", "ru": "Подтвердить платеж из банковской выписки"},
    "Potvrdi uplatu": {"en": "Confirm payment", "de": "Zahlung bestätigen", "bg": "Потвърдете плащането", "ru": "Подтвердить платеж"},
    "Nema otvorenih faktura u istoj valuti za povezivanje.": {"en": "There are no open invoices in the same currency to match.", "de": "Es gibt keine offenen Rechnungen in derselben Währung zum Abgleichen.", "bg": "Няма отворени фактури в същата валута за свързване.", "ru": "Нет открытых счетов в той же валюте для сопоставления."},
    "Kupac je sačuvan.": {"en": "Customer saved.", "de": "Kunde gespeichert.", "bg": "Клиентът е записан.", "ru": "Клиент сохранен."},
    "Kupac učitan": {"en": "Customer loaded", "de": "Kunde geladen", "bg": "Клиентът е зареден", "ru": "Клиент загружен"},
    "Nalepi jedan red iz Excela u polja projekta. Kupac ostaje izabran ručno, radi tačnog povezivanja baze.": {"en": "Paste one Excel row into the project fields. The customer remains selected manually to ensure an accurate database link.", "de": "Fügen Sie eine Excel-Zeile in die Projektfelder ein. Der Kunde wird für eine korrekte Datenbankverknüpfung manuell ausgewählt.", "bg": "Поставете един ред от Excel в полетата на проекта. Клиентът остава избран ръчно за точно свързване с базата данни.", "ru": "Вставьте одну строку из Excel в поля проекта. Клиент выбирается вручную для точной связи с базой данных."},
    "Unesite pozitivan broj, na primer 1 ili 2.": {"en": "Enter a positive number, for example 1 or 2.", "de": "Geben Sie eine positive Zahl ein, zum Beispiel 1 oder 2.", "bg": "Въведете положително число, например 1 или 2.", "ru": "Введите положительное число, например 1 или 2."},
    "Rok plaćanja mora biti broj dana.": {"en": "Payment terms must be a number of days.", "de": "Die Zahlungsfrist muss eine Anzahl von Tagen sein.", "bg": "Срокът на плащане трябва да е брой дни.", "ru": "Срок оплаты должен быть указан в днях."},
    "Novi kupac": {"en": "New customer", "de": "Neuer Kunde", "bg": "Нов клиент", "ru": "Новый клиент"},
    "Ostali troškovi": {"en": "Other costs", "de": "Sonstige Kosten", "bg": "Други разходи", "ru": "Прочие расходы"},
    "Ostali prihodi": {"en": "Other revenue", "de": "Sonstige Einnahmen", "bg": "Други приходи", "ru": "Прочие доходы"},
    "PDF račun": {"en": "PDF invoice", "de": "PDF-Rechnung", "bg": "PDF фактура", "ru": "PDF-счет"},
    "Svi fajlovi": {"en": "All files", "de": "Alle Dateien", "bg": "Всички файлове", "ru": "Все файлы"},
    "Unesite ispravan iznos bez PDV-a i stopu PDV-a.": {"en": "Enter a valid amount excluding VAT and a VAT rate.", "de": "Geben Sie einen gültigen Betrag ohne USt. und einen USt.-Satz ein.", "bg": "Въведете валидна сума без ДДС и ставка ДДС.", "ru": "Введите корректную сумму без НДС и ставку НДС."},
    "Uvezeni PDF je u valuti {currency}. OpsNest trenutno čuva samo EUR dokumente.": {"en": "The imported PDF is in {currency}. OpsNest currently stores EUR documents only.", "de": "Das importierte PDF ist in {currency}. OpsNest speichert derzeit nur EUR-Dokumente.", "bg": "Импортираният PDF е във валута {currency}. OpsNest засега съхранява само EUR документи.", "ru": "Импортированный PDF в валюте {currency}. OpsNest пока хранит только документы в EUR."},
    "Izaberite račun izdat u eurima ili unesite preračunati EUR dokument ručno.": {"en": "Select an invoice issued in euros or enter a converted EUR document manually.", "de": "Wählen Sie eine in Euro ausgestellte Rechnung oder erfassen Sie ein umgerechnetes EUR-Dokument manuell.", "bg": "Изберете фактура, издадена в евро, или въведете ръчно преизчислен EUR документ.", "ru": "Выберите счет, выставленный в евро, или вручную введите пересчитанный документ в EUR."},
    "Dodaj dokumente projektu": {"en": "Add project documents", "de": "Projektdokumente hinzufügen", "bg": "Добавете документи към проекта", "ru": "Добавить документы проекта"},
    "Dokumenti, fakture i prilozi organizovani u jednoj projektnoj arhivi.": {"en": "Documents, invoices, and attachments organized in one project archive.", "de": "Dokumente, Rechnungen und Anhänge in einem Projektarchiv organisiert.", "bg": "Документи, фактури и приложения, организирани в един архив на проекта.", "ru": "Документы, счета и вложения организованы в одном архиве проекта."},
    "Materijal": {"en": "Materials", "de": "Material", "bg": "Материали", "ru": "Материалы"},
    "Plate": {"en": "Payroll", "de": "Löhne", "bg": "Заплати", "ru": "Заработная плата"},
    "Dodaj i poveži kupca": {"en": "Add and link customer", "de": "Kunde hinzufügen und verknüpfen", "bg": "Добавете и свържете клиент", "ru": "Добавить и связать клиента"},
    "Obrisati izabrani ulazni ili izlazni račun?": {"en": "Delete the selected input or output invoice?", "de": "Die ausgewählte Eingangs- oder Ausgangsrechnung löschen?", "bg": "Да се изтрие избраната входяща или изходяща фактура?", "ru": "Удалить выбранный входящий или исходящий счет?"},
    "Snapshot kupca": {"en": "Customer snapshot", "de": "Kunden-Snapshot", "bg": "Снимка на клиента", "ru": "Снимок клиента"},
    "Brz unos stavke": {"en": "Quick line entry", "de": "Schnelle Positionserfassung", "bg": "Бързо въвеждане на позиция", "ru": "Быстрый ввод позиции"},
    "Nalepi u formu": {"en": "Paste into form", "de": "In Formular einfügen", "bg": "Поставете във формата", "ru": "Вставить в форму"},
    "Nalepi iz Excela": {"en": "Paste from Excel", "de": "Aus Excel einfügen", "bg": "Поставете от Excel", "ru": "Вставить из Excel"},
    "Izaberi priloge": {"en": "Select attachments", "de": "Anhänge auswählen", "bg": "Изберете приложения", "ru": "Выберите вложения"},
    "Sačuvaj nacrt": {"en": "Save draft", "de": "Entwurf speichern", "bg": "Запазете чернова", "ru": "Сохранить черновик"},
    "Sačuvaj i izdaj": {"en": "Save and issue", "de": "Speichern und ausstellen", "bg": "Запазете и издайте", "ru": "Сохранить и выдать"},
    "Istorija fakture": {"en": "Invoice history", "de": "Rechnungsverlauf", "bg": "История на фактурата", "ru": "История счета"},
    "Povraćaj / odobrenje": {"en": "Refund / credit note", "de": "Erstattung / Gutschrift", "bg": "Възстановяване / кредитно известие", "ru": "Возврат / кредит-нота"},
    "Izdaj formalno odobrenje": {"en": "Issue formal credit note", "de": "Formelle Gutschrift ausstellen", "bg": "Издайте официално кредитно известие", "ru": "Выдать официальную кредит-ноту"},
    "Napravi ispravku": {"en": "Create correction", "de": "Korrektur erstellen", "bg": "Създайте корекция", "ru": "Создать исправление"},
    "Storniraj fakturu": {"en": "Void invoice", "de": "Rechnung stornieren", "bg": "Анулирайте фактурата", "ru": "Аннулировать счет"},
    "Faktura koristi vaš originalni zeleni Excel šablon.": {"en": "The invoice uses your original green Excel template.", "de": "Die Rechnung verwendet Ihre originale grüne Excel-Vorlage.", "bg": "Фактурата използва вашия оригинален зелен Excel шаблон.", "ru": "Счет использует ваш исходный зеленый шаблон Excel."},
    "Popunjena kopija se otvara kroz PDF / štampu ili Excel šablon.": {"en": "The completed copy opens through PDF / print or the Excel template.", "de": "Die ausgefüllte Kopie wird über PDF / Drucken oder die Excel-Vorlage geöffnet.", "bg": "Попълненото копие се отваря чрез PDF / печат или Excel шаблона.", "ru": "Заполненная копия открывается через PDF / печать или шаблон Excel."},
    "Pogledaj prazan šablon": {"en": "View empty template", "de": "Leere Vorlage anzeigen", "bg": "Вижте празния шаблон", "ru": "Посмотреть пустой шаблон"},
    "Osveži liste": {"en": "Refresh lists", "de": "Listen aktualisieren", "bg": "Обновете списъците", "ru": "Обновить списки"},
    "Učitaj izabranu": {"en": "Load selected", "de": "Ausgewählte laden", "bg": "Заредете избраното", "ru": "Загрузить выбранное"},
    "Dupliraj izabranu": {"en": "Duplicate selected", "de": "Ausgewählte duplizieren", "bg": "Дублирайте избраното", "ru": "Дублировать выбранное"},
    "Dodaj i novo": {"en": "Add and new", "de": "Hinzufügen und neu", "bg": "Добавете и създайте ново", "ru": "Добавить и создать новый"},
    "Kopiraj": {"en": "Copy", "de": "Kopieren", "bg": "Копирайте", "ru": "Копировать"},
    "Dodaj iz projekta": {"en": "Add from project", "de": "Aus Projekt hinzufügen", "bg": "Добавете от проекта", "ru": "Добавить из проекта"},
    "Obriši uplatu": {"en": "Delete payment", "de": "Zahlung löschen", "bg": "Изтрийте плащането", "ru": "Удалить платеж"},
    "Dodaj prilog": {"en": "Add attachment", "de": "Anhang hinzufügen", "bg": "Добавете приложение", "ru": "Добавить вложение"},
    "Obriši prilog": {"en": "Delete attachment", "de": "Anhang löschen", "bg": "Изтрийте приложението", "ru": "Удалить вложение"},
    "Priprema pregleda PDF-a": {"en": "Preparing PDF preview", "de": "PDF-Vorschau wird vorbereitet", "bg": "Подготвя се PDF преглед", "ru": "Подготовка предпросмотра PDF"},
    "Datum izdavanja": {"en": "Issue date", "de": "Ausstellungsdatum", "bg": "Дата на издаване", "ru": "Дата выдачи"},
    "Datum poreskog događaja": {"en": "Tax point date", "de": "Datum des Steuertatbestands", "bg": "Дата на данъчното събитие", "ru": "Дата налогового события"},
    "Kupac - rok (dani)": {"en": "Customer - terms (days)", "de": "Kunde - Zahlungsfrist (Tage)", "bg": "Клиент - срок (дни)", "ru": "Клиент - срок оплаты (дни)"},
    "Popust / korekcija": {"en": "Discount / correction", "de": "Rabatt / Korrektur", "bg": "Отстъпка / корекция", "ru": "Скидка / корректировка"},
    "Popust %": {"en": "Discount %", "de": "Rabatt %", "bg": "Отстъпка %", "ru": "Скидка %"},
    "Broj će biti dodeljen pri čuvanju": {"en": "The number will be assigned when saved", "de": "Die Nummer wird beim Speichern vergeben", "bg": "Номерът ще бъде зададен при запис", "ru": "Номер будет присвоен при сохранении"},
    "Proverite količinu, cenu i popust.": {"en": "Check the quantity, price, and discount.", "de": "Prüfen Sie Menge, Preis und Rabatt.", "bg": "Проверете количеството, цената и отстъпката.", "ru": "Проверьте количество, цену и скидку."},
    "Ovo je nacrt. Uredite ga direktno, bez pravljenja nove ispravke.": {"en": "This is a draft. Edit it directly without creating a new correction.", "de": "Dies ist ein Entwurf. Bearbeiten Sie ihn direkt, ohne eine neue Korrektur zu erstellen.", "bg": "Това е чернова. Редактирайте я директно, без да създавате нова корекция.", "ru": "Это черновик. Редактируйте его напрямую, не создавая новое исправление."},
    "Prilog": {"en": "Attachment", "de": "Anhang", "bg": "Приложение", "ru": "Вложение"},
    "Izaberite prilog.": {"en": "Select an attachment.", "de": "Wählen Sie einen Anhang aus.", "bg": "Изберете приложение.", "ru": "Выберите вложение."},
    "Prilog nema sačuvanu putanju.": {"en": "The attachment has no saved path.", "de": "Der Anhang hat keinen gespeicherten Pfad.", "bg": "Приложението няма запазен път.", "ru": "У вложения нет сохраненного пути."},
    "Obrisati prilog?": {"en": "Delete attachment?", "de": "Anhang löschen?", "bg": "Да се изтрие приложението?", "ru": "Удалить вложение?"},
    "Nacrt je sačuvan": {"en": "Draft saved", "de": "Entwurf gespeichert", "bg": "Черновата е записана", "ru": "Черновик сохранен"},
    "Prvo sačuvajte fakturu, pa će se otvoriti popunjeni originalni šablon kao PDF.": {"en": "Save the invoice first; the completed original template will then open as a PDF.", "de": "Speichern Sie zuerst die Rechnung; anschließend wird die ausgefüllte Originalvorlage als PDF geöffnet.", "bg": "Първо запазете фактурата, след което попълненият оригинален шаблон ще се отвори като PDF.", "ru": "Сначала сохраните счет; затем заполненный исходный шаблон откроется как PDF."},
}
for _source_text, _translations in FINAL_UI_TRANSLATIONS.items():
    for _language_code, _translated_text in _translations.items():
        UI_TRANSLATIONS[_language_code][_source_text] = _translated_text


# Captions added after the first localization pass live here rather than being
# embedded in dialogs.  This keeps an already-open workspace consistent after
# the user switches the interface language.
BILLING_UI_TRANSLATIONS = {
    "Paketi i plaćanje": {"en": "Plans and billing", "de": "Pakete und Zahlung", "bg": "Планове и плащане", "ru": "Тарифы и оплата"},
    "Moj paket i plaćanje": {"en": "My plan and billing", "de": "Mein Paket und Zahlung", "bg": "Моят план и плащане", "ru": "Мой тариф и оплата"},
    "Besplatna proba": {"en": "Free trial", "de": "Kostenlose Testphase", "bg": "Безплатен пробен период", "ru": "Бесплатный пробный период"},
    "Početak probe": {"en": "Trial start", "de": "Testbeginn", "bg": "Начало на пробния период", "ru": "Начало пробного периода"},
    "Kraj probe": {"en": "Trial ends", "de": "Testende", "bg": "Край на пробния период", "ru": "Окончание пробного периода"},
    "Sledeća naplata": {"en": "Next payment", "de": "Nächste Zahlung", "bg": "Следващо плащане", "ru": "Следующий платеж"},
    "Promeni paket": {"en": "Change plan", "de": "Paket ändern", "bg": "Смяна на плана", "ru": "Изменить тариф"},
    "Otkazivanje u PayPal-u": {"en": "Cancel in PayPal", "de": "In PayPal kündigen", "bg": "Отказване в PayPal", "ru": "Отменить в PayPal"},
    "Pošalji dijagnostiku podršci": {"en": "Send diagnostics to support", "de": "Diagnose an Support senden", "bg": "Изпратете диагностика до поддръжката", "ru": "Отправить диагностику в поддержку"},
    "Proveri ažuriranja": {"en": "Check for updates", "de": "Nach Updates suchen", "bg": "Проверете за актуализации", "ru": "Проверить обновления"},
    "Osveži status": {"en": "Refresh status", "de": "Status aktualisieren", "bg": "Обновете статуса", "ru": "Обновить статус"},
    "Aktiviraj / potvrdi e-mail": {"en": "Activate / verify e-mail", "de": "E-Mail aktivieren / bestätigen", "bg": "Активирайте / потвърдете e-mail", "ru": "Активировать / подтвердить e-mail"},
    "Bezbedna dijagnostika nikada ne šalje fakture, PDF-ove, priloge, lozinke, PIN ili podatke o plaćanju.": {"en": "Safe diagnostics never send invoices, PDFs, attachments, passwords, PINs, or payment data.", "de": "Sichere Diagnosen senden niemals Rechnungen, PDFs, Anhänge, Passwörter, PINs oder Zahlungsdaten.", "bg": "Безопасната диагностика никога не изпраща фактури, PDF файлове, приложения, пароли, PIN или данни за плащане.", "ru": "Безопасная диагностика никогда не отправляет счета, PDF, вложения, пароли, PIN или платежные данные."},
    "Kupovni paket": {"en": "Purchased plan", "de": "Gebuchtes Paket", "bg": "Закупен план", "ru": "Купленный тариф"},
    "Funkcije trenutno": {"en": "Features currently available", "de": "Derzeit verfügbare Funktionen", "bg": "Налични функции в момента", "ru": "Доступные функции сейчас"},
    "Iskorišćenost ovog meseca": {"en": "Usage this month", "de": "Nutzung diesen Monat", "bg": "Използване този месец", "ru": "Использование за этот месяц"},
    "Aktivni projekti": {"en": "Active projects", "de": "Aktive Projekte", "bg": "Активни проекти", "ru": "Активные проекты"},
    "Izdate fakture": {"en": "Issued invoices", "de": "Ausgestellte Rechnungen", "bg": "Издадени фактури", "ru": "Выданные счета"},
    "PDF uvozi": {"en": "PDF imports", "de": "PDF-Importe", "bg": "PDF импортирания", "ru": "PDF-импорт"},
    "Neograničeno": {"en": "Unlimited", "de": "Unbegrenzt", "bg": "Неограничено", "ru": "Без ограничений"},
    "Status licence": {"en": "License status", "de": "Lizenzstatus", "bg": "Статус на лиценза", "ru": "Статус лицензии"},
    "OpsNest paketi": {"en": "OpsNest plans", "de": "OpsNest-Pakete", "bg": "Планове на OpsNest", "ru": "Тарифы OpsNest"},
    "Cenovnik na sajtu": {"en": "Pricing on website", "de": "Preise auf der Website", "bg": "Цени на сайта", "ru": "Цены на сайте"},
    "Izaberi paket": {"en": "Choose plan", "de": "Paket wählen", "bg": "Изберете план", "ru": "Выбрать тариф"},
    "OpsNest podrška": {"en": "OpsNest support", "de": "OpsNest-Support", "bg": "Поддръжка на OpsNest", "ru": "Поддержка OpsNest"},
    "OpsNest ažuriranje": {"en": "OpsNest update", "de": "OpsNest-Update", "bg": "Актуализация на OpsNest", "ru": "Обновление OpsNest"},
    "Zatvori": {"en": "Close", "de": "Schließen", "bg": "Затвори", "ru": "Закрыть"},
    "Status": {"en": "Status", "de": "Status", "bg": "Статус", "ru": "Статус"},
    "Firma i projekti": {"en": "Company and projects", "de": "Firma und Projekte", "bg": "Фирма и проекти", "ru": "Компания и проекты"},
    "Kupac": {"en": "Customer", "de": "Kunde", "bg": "Клиент", "ru": "Клиент"},
    "Pretraga": {"en": "Search", "de": "Suche", "bg": "Търсене", "ru": "Поиск"},
    "Traži": {"en": "Search", "de": "Suchen", "bg": "Търсене", "ru": "Поиск"},
    "Sačuvaj": {"en": "Save", "de": "Speichern", "bg": "Запази", "ru": "Сохранить"},
    "Novi projekat": {"en": "New project", "de": "Neues Projekt", "bg": "Нов проект", "ru": "Новый проект"},
    "Otvori projekat": {"en": "Open project", "de": "Projekt öffnen", "bg": "Отворете проект", "ru": "Открыть проект"},
    "Dokumenti": {"en": "Documents", "de": "Dokumente", "bg": "Документи", "ru": "Документы"},
    "Obriši": {"en": "Delete", "de": "Löschen", "bg": "Изтрий", "ru": "Удалить"},
    "Ručni unos kupca: sačuvani podaci se zatim automatski prepisuju na novu fakturu.": {"en": "Manual customer entry: saved details are then copied automatically to a new invoice.", "de": "Manuelle Kundeneingabe: gespeicherte Daten werden automatisch in eine neue Rechnung übernommen.", "bg": "Ръчно въвеждане на клиент: запазените данни се копират автоматично в нова фактура.", "ru": "Ручной ввод клиента: сохраненные данные автоматически переносятся в новый счет."},
    "Odgovorno lice": {"en": "Contact person", "de": "Ansprechperson", "bg": "Лице за контакт", "ru": "Контактное лицо"},
    "Rok plaćanja (dani)": {"en": "Payment terms (days)", "de": "Zahlungsfrist (Tage)", "bg": "Срок за плащане (дни)", "ru": "Срок оплаты (дни)"},
    "Napomena": {"en": "Note", "de": "Notiz", "bg": "Бележка", "ru": "Примечание"},
    "Broj računa / dokumenta": {"en": "Invoice / document number", "de": "Rechnungs- / Dokumentnummer", "bg": "Номер на фактура / документ", "ru": "Номер счета / документа"},
    "Dobavljač / kupac": {"en": "Supplier / customer", "de": "Lieferant / Kunde", "bg": "Доставчик / клиент", "ru": "Поставщик / клиент"},
    "Iznos bez PDV-a": {"en": "Amount excl. VAT", "de": "Betrag ohne USt.", "bg": "Сума без ДДС", "ru": "Сумма без НДС"},
    "Ukupno sa PDV-om": {"en": "Total incl. VAT", "de": "Gesamt inkl. USt.", "bg": "Общо с ДДС", "ru": "Итого с НДС"},
    "Projekat": {"en": "Project", "de": "Projekt", "bg": "Проект", "ru": "Проект"},
    "Sledeća faktura": {"en": "Next invoice", "de": "Nächste Rechnung", "bg": "Следваща фактура", "ru": "Следующий счет"},
    "Naziv projekta": {"en": "Project name", "de": "Projektname", "bg": "Име на проекта", "ru": "Название проекта"},
    "Oznaka bloka faktura": {"en": "Invoice number block", "de": "Rechnungsnummernblock", "bg": "Блок номера на фактури", "ru": "Блок номеров счетов"},
    "Adresa gradilišta": {"en": "Site address", "de": "Baustellenadresse", "bg": "Адрес на обекта", "ru": "Адрес объекта"},
    "Broj ugovora": {"en": "Contract number", "de": "Vertragsnummer", "bg": "Номер на договор", "ru": "Номер договора"},
    "Broj protokola / Akta 19": {"en": "Protocol / Act 19 number", "de": "Protokoll- / Akt-19-Nummer", "bg": "Номер на протокол / Акт 19", "ru": "Номер протокола / Акта 19"},
    "Period od (dd.mm.yyyy)": {"en": "Period from (dd.mm.yyyy)", "de": "Zeitraum ab (TT.MM.JJJJ)", "bg": "Период от (дд.мм.гггг)", "ru": "Период с (дд.мм.гггг)"},
    "Period do (dd.mm.yyyy)": {"en": "Period to (dd.mm.yyyy)", "de": "Zeitraum bis (TT.MM.JJJJ)", "bg": "Период до (дд.мм.гггг)", "ru": "Период до (дд.мм.гггг)"},
    "Poređenja / referenca": {"en": "Reference", "de": "Referenz", "bg": "Референция", "ru": "Ссылка"},
    "Uvezite CSV ili XLSX izvod. OpsNest samo predlaže fakturu, a uplata se knjiži tek nakon vaše potvrde.": {"en": "Import a CSV or XLSX statement. OpsNest only suggests an invoice; payment is posted only after your confirmation.", "de": "Importieren Sie einen CSV- oder XLSX-Kontoauszug. OpsNest schlägt nur eine Rechnung vor; die Zahlung wird erst nach Ihrer Bestätigung gebucht.", "bg": "Импортирайте CSV или XLSX извлечение. OpsNest само предлага фактура; плащането се осчетоводява след вашето потвърждение.", "ru": "Импортируйте выписку CSV или XLSX. OpsNest только предлагает счет; платеж проводится только после вашего подтверждения."},
}
for _source_text, _translations in BILLING_UI_TRANSLATIONS.items():
    for _language_code, _translated_text in _translations.items():
        UI_TRANSLATIONS[_language_code][_source_text] = _translated_text


SUPPORT_UI_TRANSLATIONS = {
    "Kako podrška može da pomogne?": {"en": "How can support help?", "de": "Wie kann der Support helfen?", "bg": "Как може да помогне поддръжката?", "ru": "Как может помочь поддержка?"},
    "Pošaljite kratak opis problema. Ako se odnosi na dokument, navedite broj fakture ili projekta, ali ne unosite lozinke, PIN-ove ili podatke kartice.": {"en": "Send a short description of the issue. If it relates to a document, include the invoice or project number, but never enter passwords, PINs or card details.", "de": "Senden Sie eine kurze Problembeschreibung. Bei einem Dokument nennen Sie bitte die Rechnungs- oder Projektnummer, jedoch niemals Passwörter, PINs oder Kartendaten.", "bg": "Изпратете кратко описание на проблема. Ако е свързан с документ, посочете номера на фактурата или проекта, но не въвеждайте пароли, PIN кодове или данни за карта.", "ru": "Отправьте краткое описание проблемы. Если она связана с документом, укажите номер счета или проекта, но не вводите пароли, PIN-коды или данные карты."},
    "Šta se bezbedno šalje podršci": {"en": "What is safely sent to support", "de": "Was sicher an den Support gesendet wird", "bg": "Какво се изпраща безопасно до поддръжката", "ru": "Что безопасно отправляется в поддержку"},
    "Samo verzija aplikacije, operativni sistem, status licence i opis koji ovde unesete.": {"en": "Only the application version, operating system, license status and the description you enter here.", "de": "Nur die Anwendungsversion, das Betriebssystem, der Lizenzstatus und die hier eingegebene Beschreibung.", "bg": "Само версията на приложението, операционната система, статусът на лиценза и описанието, което въведете тук.", "ru": "Только версия приложения, операционная система, статус лицензии и описание, которое вы введете здесь."},
    "Fakture, PDF-ovi, prilozi, lozinke, PIN-ovi, bankovni i kartični podaci nikada se ne šalju.": {"en": "Invoices, PDFs, attachments, passwords, PINs, bank and card details are never sent.", "de": "Rechnungen, PDFs, Anhänge, Passwörter, PINs sowie Bank- und Kartendaten werden niemals gesendet.", "bg": "Фактури, PDF файлове, приложения, пароли, PIN кодове, банкови и картови данни никога не се изпращат.", "ru": "Счета, PDF-файлы, вложения, пароли, PIN-коды, банковские и карточные данные никогда не отправляются."},
    "Opis problema (opciono)": {"en": "Issue description (optional)", "de": "Problembeschreibung (optional)", "bg": "Описание на проблема (по избор)", "ru": "Описание проблемы (необязательно)"},
    "Pošalji bezbednu dijagnostiku": {"en": "Send safe diagnostics", "de": "Sichere Diagnose senden", "bg": "Изпратете безопасна диагностика", "ru": "Отправить безопасную диагностику"},
}
for _source_text, _translations in SUPPORT_UI_TRANSLATIONS.items():
    for _language_code, _translated_text in _translations.items():
        UI_TRANSLATIONS[_language_code][_source_text] = _translated_text


# Captions used by the country, registration and team-management flows.  They
# are kept in the same central table as the rest of the desktop UI so changing
# language never leaves these screens half-translated.
COMMERCIAL_UI_TRANSLATIONS = {
    "Država registracije": {"en": "Country of registration", "de": "Registrierungsland", "bg": "Държава на регистрация", "ru": "Страна регистрации"},
    "Država": {"en": "Country", "de": "Land", "bg": "Държава", "ru": "Страна"},
    "Podrazumevana PDV stopa": {"en": "Default VAT rate", "de": "Standard-USt.-Satz", "bg": "Стандартна ставка на ДДС", "ru": "Стандартная ставка НДС"},
    "Država postavlja podrazumevanu standardnu PDV stopu. Stopu možete promeniti na svakoj fakturi.": {"en": "The country sets the default standard VAT rate. You can change the rate on every invoice.", "de": "Das Land legt den Standard-USt.-Satz fest. Sie können den Satz auf jeder Rechnung ändern.", "bg": "Държавата задава стандартната ставка на ДДС. Можете да я промените за всяка фактура.", "ru": "Страна задает стандартную ставку НДС. Ее можно изменить в каждом счете."},
    "Korisnici firme": {"en": "Company users", "de": "Firmenbenutzer", "bg": "Потребители на фирмата", "ru": "Пользователи компании"},
    "Korisnička mesta": {"en": "User seats", "de": "Benutzerplätze", "bg": "Потребителски места", "ru": "Пользовательские места"},
    "Plan i mesta": {"en": "Plan and seats", "de": "Paket und Plätze", "bg": "План и места", "ru": "Тариф и места"},
    "Vlasnik (lokalni profil)": {"en": "Owner (local profile)", "de": "Inhaber (lokales Profil)", "bg": "Собственик (локален профил)", "ru": "Владелец (локальный профиль)"},
    "Ime korisnika": {"en": "User name", "de": "Benutzername", "bg": "Име на потребителя", "ru": "Имя пользователя"},
    "Uloga": {"en": "Role", "de": "Rolle", "bg": "Роля", "ru": "Роль"},
    "Član": {"en": "Member", "de": "Mitglied", "bg": "Член", "ru": "Участник"},
    "Knjigovođa": {"en": "Accountant", "de": "Buchhalter", "bg": "Счетоводител", "ru": "Бухгалтер"},
    "Pozvan": {"en": "Invited", "de": "Eingeladen", "bg": "Поканен", "ru": "Приглашен"},
    "Aktivan": {"en": "Active", "de": "Aktiv", "bg": "Активен", "ru": "Активен"},
    "Dodaj korisnika": {"en": "Add user", "de": "Benutzer hinzufügen", "bg": "Добавете потребител", "ru": "Добавить пользователя"},
    "Sačuvaj korisnika": {"en": "Save user", "de": "Benutzer speichern", "bg": "Запазете потребителя", "ru": "Сохранить пользователя"},
    "Ukloni korisnika": {"en": "Remove user", "de": "Benutzer entfernen", "bg": "Премахнете потребителя", "ru": "Удалить пользователя"},
    "Novi korisnik": {"en": "New user", "de": "Neuer Benutzer", "bg": "Нов потребител", "ru": "Новый пользователь"},
    "Otvoriti pakete i plaćanje?": {"en": "Open plans and billing?", "de": "Pakete und Zahlung öffnen?", "bg": "Да се отворят планове и плащане?", "ru": "Открыть тарифы и оплату?"},
    "OpsNest paket": {"en": "OpsNest plan", "de": "OpsNest-Paket", "bg": "Пакет OpsNest", "ru": "Тариф OpsNest"},
    "Spisak tima trenutno čuva mesta, uloge i e-mail adrese. Posebne cloud prijave i zajednička sinhronizacija dolaze u sledećoj fazi.": {"en": "The team list currently stores seats, roles and e-mail addresses. Separate cloud sign-ins and shared sync are planned for the next phase.", "de": "Die Teamliste speichert derzeit Plätze, Rollen und E-Mail-Adressen. Separate Cloud-Anmeldungen und gemeinsame Synchronisierung folgen in der nächsten Phase.", "bg": "Списъкът на екипа засега пази места, роли и e-mail адреси. Отделни cloud входове и обща синхронизация идват в следваща фаза.", "ru": "Список команды пока хранит места, роли и e-mail адреса. Отдельные облачные входы и общая синхронизация появятся на следующем этапе."},
    "Za dodavanje korisnika potreban je Business ili Pro paket.": {"en": "Business or Pro is required to add users.", "de": "Zum Hinzufügen von Benutzern ist Business oder Pro erforderlich.", "bg": "За добавяне на потребители е нужен пакет Business или Pro.", "ru": "Для добавления пользователей нужен Business или Pro."},
    "Naziv projekta": {"en": "Project name", "de": "Projektname", "bg": "Име на проекта", "ru": "Название проекта"},
    "Oznaka bloka faktura": {"en": "Invoice number block", "de": "Rechnungsnummernblock", "bg": "Блок номера на фактури", "ru": "Блок номеров счетов"},
    "Adresa gradilišta": {"en": "Site address", "de": "Baustellenadresse", "bg": "Адрес на обекта", "ru": "Адрес объекта"},
    "Broj ugovora": {"en": "Contract number", "de": "Vertragsnummer", "bg": "Номер на договор", "ru": "Номер договора"},
    "Broj protokola / Akta 19": {"en": "Protocol / Act 19 number", "de": "Protokoll- / Akt-19-Nummer", "bg": "Номер на протокол / Акт 19", "ru": "Номер протокола / Акта 19"},
    "Poređenja / referenca": {"en": "Reference", "de": "Referenz", "bg": "Референция", "ru": "Ссылка"},
    "Ako ostavite prazno, program dodeljuje prvi slobodan blok. Primer: 1 -> 1000000001.": {"en": "If left empty, OpsNest assigns the first available block. Example: 1 -> 1000000001.", "de": "Wenn leer, weist OpsNest den ersten freien Block zu. Beispiel: 1 -> 1000000001.", "bg": "Ако оставите празно, OpsNest задава първия свободен блок. Пример: 1 -> 1000000001.", "ru": "Если оставить пустым, OpsNest назначит первый свободный блок. Пример: 1 -> 1000000001."},
    "Projekat je glavna jedinica rada. Kupac je opcioni podatak projekta; na svakoj fakturi birate konkretnog kupca.": {"en": "A project is the main work unit. The customer is optional project data; choose the specific customer on every invoice.", "de": "Ein Projekt ist die zentrale Arbeitseinheit. Der Kunde ist eine optionale Projektangabe; wählen Sie den konkreten Kunden auf jeder Rechnung.", "bg": "Проектът е основната работна единица. Клиентът е незадължителна информация за проекта; за всяка фактура избирате конкретния клиент.", "ru": "Проект является основной единицей работы. Клиент - необязательная информация проекта; в каждом счете выбирается конкретный клиент."},
}
for _source_text, _translations in COMMERCIAL_UI_TRANSLATIONS.items():
    for _language_code, _translated_text in _translations.items():
        UI_TRANSLATIONS[_language_code][_source_text] = _translated_text


# Workflow captions are deliberately centralized here: these dialogs are opened
# after the main screen has already changed language, so every visible caption
# and status must use the same translation table as the rest of the app.
WORKFLOW_UI_TRANSLATIONS = {
    "Na proveri": {"en": "Pending approval", "de": "Zur Prüfung", "bg": "За одобрение", "ru": "На проверке"},
    "Odobrena": {"en": "Approved", "de": "Genehmigt", "bg": "Одобрена", "ru": "Одобрено"},
    "Dnevni centar": {"en": "Daily work center", "de": "Tägliches Arbeitszentrum", "bg": "Дневен работен център", "ru": "Ежедневный рабочий центр"},
    "Obaveza": {"en": "Action", "de": "Aufgabe", "bg": "Задача", "ru": "Задача"},
    "Dokument": {"en": "Document", "de": "Dokument", "bg": "Документ", "ru": "Документ"},
    "Kupac / dobavljač": {"en": "Customer / supplier", "de": "Kunde / Lieferant", "bg": "Клиент / доставчик", "ru": "Клиент / поставщик"},
    "Rok / detalj": {"en": "Due date / details", "de": "Fälligkeit / Details", "bg": "Срок / детайли", "ru": "Срок / детали"},
    "Dospeli kupac": {"en": "Overdue customer", "de": "Überfälliger Kunde", "bg": "Просрочен клиент", "ru": "Просроченный клиент"},
    "Faktura dospeva uskoro": {"en": "Invoice due soon", "de": "Rechnung bald fällig", "bg": "Фактурата скоро е изискуема", "ru": "Счет скоро подлежит оплате"},
    "PDF prilog nedostaje": {"en": "PDF attachment missing", "de": "PDF-Anhang fehlt", "bg": "Липсва PDF приложение", "ru": "Отсутствует PDF-вложение"},
    "Budžet prekoračen": {"en": "Budget exceeded", "de": "Budget überschritten", "bg": "Бюджетът е надвишен", "ru": "Бюджет превышен"},
    "Sve je pod kontrolom": {"en": "Everything is under control", "de": "Alles ist unter Kontrolle", "bg": "Всичко е под контрол", "ru": "Все под контролем"},
    "Nema otvorenih obaveza": {"en": "No open actions", "de": "Keine offenen Aufgaben", "bg": "Няма отворени задачи", "ru": "Нет открытых задач"},
    "Rok {date}": {"en": "due {date}", "de": "fällig am {date}", "bg": "срок {date}", "ru": "срок {date}"},
    "Dospeva u 7 dana": {"en": "Due within 7 days", "de": "Fällig in 7 Tagen", "bg": "Падеж след 7 дни", "ru": "Срок в течение 7 дней"},
    "Ulazni račun": {"en": "Incoming invoice", "de": "Eingangsrechnung", "bg": "Входяща фактура", "ru": "Входящий счет"},
    "Bez broja": {"en": "No number", "de": "Ohne Nummer", "bg": "Без номер", "ru": "Без номера"},
    "Ulazni račun bez PDF-a": {"en": "Incoming invoice without PDF", "de": "Eingangsrechnung ohne PDF", "bg": "Входяща фактура без PDF", "ru": "Входящий счет без PDF"},
    "Trošak iznad budžeta": {"en": "Cost above budget", "de": "Kosten über Budget", "bg": "Разход над бюджета", "ru": "Расход выше бюджета"},
    "Uneti trošak je veći od planiranog budžeta.": {"en": "The entered cost is higher than the planned budget.", "de": "Die erfassten Kosten sind höher als das geplante Budget.", "bg": "Въведеният разход е по-висок от планирания бюджет.", "ru": "Введенный расход выше запланированного бюджета."},
    "Nema dospelih faktura, rokova, PDF provera niti prekoračenja budžeta.": {"en": "No overdue invoices, deadlines, missing PDFs or budget overruns.", "de": "Keine überfälligen Rechnungen, Fristen, fehlenden PDFs oder Budgetüberschreitungen.", "bg": "Няма просрочени фактури, срокове, липсващи PDF или надвишен бюджет.", "ru": "Нет просроченных счетов, сроков, отсутствующих PDF или превышения бюджета."},
    "Trošak je veći od planiranog": {"en": "Cost is higher than planned", "de": "Kosten sind höher als geplant", "bg": "Разходът е по-висок от планираното", "ru": "Расход выше запланированного"},
    "Ponavljajuće fakture": {"en": "Recurring invoices", "de": "Wiederkehrende Rechnungen", "bg": "Повтарящи се фактури", "ru": "Повторяющиеся счета"},
    "Na dan dospeća OpsNest kreira nacrt. Pre izdavanja uvek pregledate i potvrdite fakturu.": {"en": "On the due date, OpsNest creates a draft. You always review and confirm the invoice before issuing it.", "de": "Am Fälligkeitstag erstellt OpsNest einen Entwurf. Sie prüfen und bestätigen die Rechnung immer vor der Ausstellung.", "bg": "На падежа OpsNest създава чернова. Винаги преглеждате и потвърждавате фактурата преди издаване.", "ru": "В дату срока OpsNest создает черновик. Перед выпуском вы всегда проверяете и подтверждаете счет."},
    "Period": {"en": "Interval", "de": "Intervall", "bg": "Период", "ru": "Период"},
    "Sledeći nacrt": {"en": "Next draft", "de": "Nächster Entwurf", "bg": "Следваща чернова", "ru": "Следующий черновик"},
    "Poslednja faktura": {"en": "Latest invoice", "de": "Letzte Rechnung", "bg": "Последна фактура", "ru": "Последний счет"},
    "Kreiraj dospele nacrte": {"en": "Create due drafts", "de": "Fällige Entwürfe erstellen", "bg": "Създайте дължимите чернови", "ru": "Создать черновики по сроку"},
    "Aktiviraj / pauziraj": {"en": "Activate / pause", "de": "Aktivieren / pausieren", "bg": "Активиране / пауза", "ru": "Активировать / приостановить"},
    "Otvori poslednji nacrt": {"en": "Open latest draft", "de": "Letzten Entwurf öffnen", "bg": "Отворете последната чернова", "ru": "Открыть последний черновик"},
    "Aktivna": {"en": "Active", "de": "Aktiv", "bg": "Активна", "ru": "Активна"},
    "Pauzirana": {"en": "Paused", "de": "Pausiert", "bg": "На пауза", "ru": "Приостановлена"},
    "svakih {count} mes.": {"en": "every {count} month(s)", "de": "alle {count} Monat(e)", "bg": "на всеки {count} мес.", "ru": "каждые {count} мес."},
    "Sačuvaj kao ponavljajuću fakturu": {"en": "Save as recurring invoice", "de": "Als wiederkehrende Rechnung speichern", "bg": "Запазете като повтаряща се фактура", "ru": "Сохранить как повторяющийся счет"},
    "Ponavljajuća faktura": {"en": "Recurring invoice", "de": "Wiederkehrende Rechnung", "bg": "Повтаряща се фактура", "ru": "Повторяющийся счет"},
    "Sačuvaćemo trenutne stavke kao šablon. Na svaki rok nastaje nacrt koji se pregleda pre izdavanja.": {"en": "The current items will be saved as a template. Each due date creates a draft that is reviewed before issue.", "de": "Die aktuellen Positionen werden als Vorlage gespeichert. Zu jedem Termin entsteht ein Entwurf, der vor der Ausstellung geprüft wird.", "bg": "Текущите позиции ще бъдат запазени като шаблон. На всеки срок се създава чернова за преглед преди издаване.", "ru": "Текущие позиции сохраняются как шаблон. В каждый срок создается черновик для проверки перед выпуском."},
    "Naziv šablona": {"en": "Template name", "de": "Vorlagenname", "bg": "Име на шаблон", "ru": "Название шаблона"},
    "Ponavlja se na (meseci)": {"en": "Repeat every (months)", "de": "Wiederholen alle (Monate)", "bg": "Повтаря се на (месеци)", "ru": "Повторять каждые (месяцев)"},
    "Prvi nacrt": {"en": "First draft", "de": "Erster Entwurf", "bg": "Първа чернова", "ru": "Первый черновик"},
    "Sačuvaj šablon": {"en": "Save template", "de": "Vorlage speichern", "bg": "Запазете шаблона", "ru": "Сохранить шаблон"},
    "Podsetnik za plaćanje": {"en": "Payment reminder", "de": "Zahlungserinnerung", "bg": "Напомняне за плащане", "ru": "Напоминание об оплате"},
    "Podsetnik kupcu": {"en": "Customer reminder", "de": "Kundenerinnerung", "bg": "Напомняне до клиента", "ru": "Напоминание клиенту"},
    "Primaoc": {"en": "Recipient", "de": "Empfänger", "bg": "Получател", "ru": "Получатель"},
    "Naslov": {"en": "Subject", "de": "Betreff", "bg": "Тема", "ru": "Тема"},
    "Poruka": {"en": "Message", "de": "Nachricht", "bg": "Съобщение", "ru": "Сообщение"},
    "Pošalji podsetnik": {"en": "Send reminder", "de": "Erinnerung senden", "bg": "Изпратете напомняне", "ru": "Отправить напоминание"},
    "Slanje se beleži u istoriji fakture. Faktura i prilozi se ne šalju automatski.": {"en": "Sending is recorded in the invoice history. The invoice and attachments are not sent automatically.", "de": "Der Versand wird in der Rechnungshistorie protokolliert. Rechnung und Anhänge werden nicht automatisch gesendet.", "bg": "Изпращането се записва в историята на фактурата. Фактурата и приложенията не се изпращат автоматично.", "ru": "Отправка записывается в истории счета. Счет и вложения не отправляются автоматически."},
    "Faktura {number} | otvoreno {amount} | rok {date}": {"en": "Invoice {number} | outstanding {amount} | due {date}", "de": "Rechnung {number} | offen {amount} | fällig {date}", "bg": "Фактура {number} | неплатено {amount} | срок {date}", "ru": "Счет {number} | открыто {amount} | срок {date}"},
    "Podsetnik može da se šalje tek kada se faktura izda.": {"en": "A reminder can only be sent after the invoice is issued.", "de": "Eine Erinnerung kann erst nach Ausstellung der Rechnung gesendet werden.", "bg": "Напомняне може да се изпрати едва след издаване на фактурата.", "ru": "Напоминание можно отправить только после выпуска счета."},
    "Unesite ispravan e-mail primaoca.": {"en": "Enter a valid recipient e-mail.", "de": "Geben Sie eine gültige Empfänger-E-Mail ein.", "bg": "Въведете валиден e-mail на получателя.", "ru": "Введите действительный e-mail получателя."},
    "Podsetnik je poslat i upisan u istoriju fakture.": {"en": "The reminder was sent and recorded in the invoice history.", "de": "Die Erinnerung wurde gesendet und in der Rechnungshistorie protokolliert.", "bg": "Напомнянето е изпратено и записано в историята на фактурата.", "ru": "Напоминание отправлено и записано в истории счета."},
    "Automatski podsetnici za dospele fakture": {"en": "Automatic reminders for overdue invoices", "de": "Automatische Erinnerungen für überfällige Rechnungen", "bg": "Автоматични напомняния за просрочени фактури", "ru": "Автоматические напоминания о просроченных счетах"},
    "Šalje se samo tekst podsetnika kupcu kada faktura dospe, a zatim najviše jednom u izabranom broju dana. Fakture i prilozi se nikada ne šalju automatski.": {"en": "Only a text reminder is sent to the customer when an invoice is due, then at most once per selected number of days. Invoices and attachments are never sent automatically.", "de": "Es wird nur ein Texthinweis an den Kunden gesendet, wenn eine Rechnung fällig ist, danach höchstens einmal pro gewählter Anzahl von Tagen. Rechnungen und Anhänge werden niemals automatisch gesendet.", "bg": "Изпраща се само текстово напомняне до клиента при падеж на фактурата, след това най-много веднъж на избрания брой дни. Фактури и приложения никога не се изпращат автоматично.", "ru": "Клиенту отправляется только текстовое напоминание при наступлении срока счета, затем не чаще одного раза за выбранное число дней. Счета и вложения никогда не отправляются автоматически."},
    "Uključi automatske podsetnike": {"en": "Enable automatic reminders", "de": "Automatische Erinnerungen aktivieren", "bg": "Включете автоматични напомняния", "ru": "Включить автоматические напоминания"},
    "Razmak podsetnika (dani)": {"en": "Reminder interval (days)", "de": "Erinnerungsintervall (Tage)", "bg": "Интервал на напомняне (дни)", "ru": "Интервал напоминаний (дни)"},
    "Automatski je poslato {count} podsetnika za dospele fakture.": {"en": "{count} automatic reminders for due invoices were sent.", "de": "{count} automatische Erinnerungen für fällige Rechnungen wurden gesendet.", "bg": "Изпратени са {count} автоматични напомняния за изискуеми фактури.", "ru": "Автоматически отправлено напоминаний по просроченным счетам: {count}."},
    "Kreirano je {count} ponavljajućih nacrta za proveru.": {"en": "{count} recurring drafts were created for review.", "de": "{count} wiederkehrende Entwürfe wurden zur Prüfung erstellt.", "bg": "Създадени са {count} повтарящи се чернови за преглед.", "ru": "Создано повторяющихся черновиков для проверки: {count}."},
    "Istorija fakture": {"en": "Invoice history", "de": "Rechnungshistorie", "bg": "История на фактурата", "ru": "История счета"},
    "Vreme": {"en": "Time", "de": "Zeit", "bg": "Време", "ru": "Время"},
    "Akcija": {"en": "Action", "de": "Aktion", "bg": "Действие", "ru": "Действие"},
    "Detalj": {"en": "Details", "de": "Details", "bg": "Детайли", "ru": "Детали"},
    "Kreirana": {"en": "Created", "de": "Erstellt", "bg": "Създадена", "ru": "Создан"},
    "Izmenjena": {"en": "Updated", "de": "Geändert", "bg": "Променена", "ru": "Изменен"},
    "Promena statusa": {"en": "Status changed", "de": "Status geändert", "bg": "Промяна на статус", "ru": "Изменение статуса"},
    "Poslata na proveru": {"en": "Submitted for approval", "de": "Zur Prüfung gesendet", "bg": "Изпратена за одобрение", "ru": "Отправлен на проверку"},
    "Odobrena za izdavanje": {"en": "Approved for issue", "de": "Zur Ausstellung freigegeben", "bg": "Одобрена за издаване", "ru": "Одобрен для выпуска"},
    "Izdata nakon odobrenja": {"en": "Issued after approval", "de": "Nach Genehmigung ausgestellt", "bg": "Издадена след одобрение", "ru": "Выпущен после одобрения"},
    "Poslat podsetnik za plaćanje": {"en": "Payment reminder sent", "de": "Zahlungserinnerung gesendet", "bg": "Изпратено напомняне за плащане", "ru": "Напоминание об оплате отправлено"},
    "Kreiran ponavljajući nacrt": {"en": "Recurring draft created", "de": "Wiederkehrender Entwurf erstellt", "bg": "Създадена повтаряща се чернова", "ru": "Создан повторяющийся черновик"},
}
for _source_text, _translations in WORKFLOW_UI_TRANSLATIONS.items():
    for _language_code, _translated_text in _translations.items():
        UI_TRANSLATIONS[_language_code][_source_text] = _translated_text


PAYMENT_REMINDER_COPY = {
    "sr": {
        "subject": "Podsetnik za plaćanje - faktura {number}",
        "body": "Poštovani {customer},\n\nljubazno vas podsećamo da je za fakturu {number} preostao iznos za plaćanje {amount}. Rok plaćanja je {due}.\n\nAko je uplata već izvršena, molimo zanemarite ovu poruku.\n\nSrdačan pozdrav,\n{company}",
    },
    "en": {
        "subject": "Payment reminder - invoice {number}",
        "body": "Dear {customer},\n\nthis is a friendly reminder that {amount} remains outstanding on invoice {number}. The due date is {due}.\n\nIf payment has already been made, please disregard this message.\n\nKind regards,\n{company}",
    },
    "de": {
        "subject": "Zahlungserinnerung - Rechnung {number}",
        "body": "Guten Tag {customer},\n\nwir möchten Sie freundlich daran erinnern, dass für Rechnung {number} noch {amount} offen sind. Das Zahlungsziel ist der {due}.\n\nFalls die Zahlung bereits erfolgt ist, betrachten Sie diese Nachricht bitte als gegenstandslos.\n\nMit freundlichen Grüßen\n{company}",
    },
    "bg": {
        "subject": "Напомняне за плащане - фактура {number}",
        "body": "Здравейте {customer},\n\nучтиво Ви напомняме, че по фактура {number} остава сума за плащане {amount}. Срокът за плащане е {due}.\n\nАко плащането вече е извършено, моля игнорирайте това съобщение.\n\nПоздрави,\n{company}",
    },
    "ru": {
        "subject": "Напоминание об оплате - счет {number}",
        "body": "Здравствуйте, {customer}!\n\nНапоминаем, что по счету {number} остается к оплате {amount}. Срок оплаты: {due}.\n\nЕсли оплата уже произведена, пожалуйста, проигнорируйте это сообщение.\n\nС уважением,\n{company}",
    },
}


def payment_reminder_copy(key: str, language: str | None = None, **values: Any) -> str:
    code = normalize_ui_language(language or active_ui_language())
    text = PAYMENT_REMINDER_COPY.get(code, PAYMENT_REMINDER_COPY["sr"]).get(key, PAYMENT_REMINDER_COPY["sr"][key])
    return text.format(**values)


# Dynamic package-card sentences are composed from usage limits, so they need
# their own localized templates instead of leaving Serbian fragments inside an
# otherwise translated dialog.
PLAN_DIALOG_COPY = {
    "sr": {
        "trial_explainer": "Probni period počinje kada se firma registruje. Potvrda poslovnog e-maila samo bezbedno povezuje plaćanje i podršku.",
        "per_month": "EUR / mesec",
        "unlimited": "Neograničeno",
        "card_limits": "{seats} korisničkih mesta\n{projects} projekata\n{invoices} faktura / mesec\n{pdfs} PDF uvoza / mesec",
        "not_registered": "Registrujte firmu da besplatna proba odmah počne.",
        "latest_update": "Koristite najnoviju dostupnu verziju: {version}.",
        "update_available": "Dostupna je verzija {version}. Otvoriti bezbedan installer link?",
        "installer_pending": "Dostupna je verzija {version}, ali installer link još nije objavljen.",
        "update_metadata_pending": "Dostupna je verzija {version}, ali za automatsko ažuriranje još nije objavljen sigurnosni potpis. Otvoriti zvanični installer link?",
        "update_ready": "Dostupna je verzija {version}. Preuzimanje ostaje u aplikaciji, a zatim OpsNest sam pokreće bezbedno ažuriranje.",
        "update_safety_title": "Šta ažuriranje menja",
        "update_safety": "Menjaju se samo programski fajlovi. Baza, fakture, PDF-ovi, prilozi i lokalni podaci ostaju netaknuti.",
        "update_waiting": "Spremno za bezbedno preuzimanje.",
        "download_install": "Preuzmi i instaliraj",
        "update_downloading": "Preuzimanje ažuriranja: {percent}%.",
        "update_downloading_unknown": "Preuzimanje ažuriranja...",
        "update_integrity_failed": "Bezbednosna provera ažuriranja nije uspela. Fajl nije instaliran.",
        "update_download_failed": "Preuzimanje nije uspelo. Proverite internet vezu i pokušajte ponovo.",
        "update_downloaded": "Ažuriranje je provereno i spremno za instalaciju.",
        "install_restart": "Instaliraj i ponovo pokreni",
        "update_restart_question": "OpsNest će se zatvoriti, ažurirati na verziju {version} i odmah ponovo pokrenuti. Lokalni podaci ostaju sačuvani. Nastaviti?",
        "update_manual_development": "Automatsko ažuriranje radi iz instalirane Windows aplikacije. Razvojna verzija se ne menja automatski.",
        "support_prompt": "Kratko opišite problem (opciono):",
    },
    "en": {
        "trial_explainer": "The trial starts when the company is registered. Business e-mail verification only connects billing and support securely.",
        "per_month": "EUR / month",
        "unlimited": "Unlimited",
        "card_limits": "{seats} team seats\n{projects} projects\n{invoices} invoices / month\n{pdfs} PDF imports / month",
        "not_registered": "Register a company to start the free trial immediately.",
        "latest_update": "You are using the latest available version: {version}.",
        "update_available": "Version {version} is available. Open the secure installer link?",
        "installer_pending": "Version {version} is available, but the installer link has not been published yet.",
        "update_metadata_pending": "Version {version} is available, but its security checksum is not published yet. Open the official installer link?",
        "update_ready": "Version {version} is available. The download remains in the app, then OpsNest starts the secure update automatically.",
        "update_safety_title": "What the update changes",
        "update_safety": "Only program files are replaced. Your database, invoices, PDFs, attachments, and local data remain untouched.",
        "update_waiting": "Ready for a secure download.",
        "download_install": "Download and install",
        "update_downloading": "Downloading update: {percent}%.",
        "update_downloading_unknown": "Downloading update...",
        "update_integrity_failed": "The update security check failed. The file was not installed.",
        "update_download_failed": "The download failed. Check your internet connection and try again.",
        "update_downloaded": "The update was verified and is ready to install.",
        "install_restart": "Install and restart",
        "update_restart_question": "OpsNest will close, update to version {version}, and restart immediately. Local data will remain saved. Continue?",
        "update_manual_development": "Automatic updates run from the installed Windows app. The development version is not changed automatically.",
        "support_prompt": "Briefly describe the issue (optional):",
    },
    "de": {
        "trial_explainer": "Die Testphase beginnt bei der Firmenregistrierung. Die Bestätigung der geschäftlichen E-Mail verbindet nur Zahlung und Support sicher.",
        "per_month": "EUR / Monat",
        "unlimited": "Unbegrenzt",
        "card_limits": "{seats} Benutzerplätze\n{projects} Projekte\n{invoices} Rechnungen / Monat\n{pdfs} PDF-Importe / Monat",
        "not_registered": "Registrieren Sie eine Firma, damit die kostenlose Testphase sofort beginnt.",
        "latest_update": "Sie verwenden die neueste verfügbare Version: {version}.",
        "update_available": "Version {version} ist verfügbar. Den sicheren Installer-Link öffnen?",
        "installer_pending": "Version {version} ist verfügbar, aber der Installer-Link wurde noch nicht veröffentlicht.",
        "update_metadata_pending": "Version {version} ist verfügbar, aber ihre Sicherheits-Prüfsumme wurde noch nicht veröffentlicht. Den offiziellen Installer-Link öffnen?",
        "update_ready": "Version {version} ist verfügbar. Der Download bleibt in der App, danach startet OpsNest die sichere Aktualisierung automatisch.",
        "update_safety_title": "Was das Update ändert",
        "update_safety": "Nur Programmdateien werden ersetzt. Datenbank, Rechnungen, PDFs, Anhänge und lokale Daten bleiben unverändert.",
        "update_waiting": "Bereit für einen sicheren Download.",
        "download_install": "Herunterladen und installieren",
        "update_downloading": "Update wird heruntergeladen: {percent}%.",
        "update_downloading_unknown": "Update wird heruntergeladen...",
        "update_integrity_failed": "Die Sicherheitsprüfung des Updates ist fehlgeschlagen. Die Datei wurde nicht installiert.",
        "update_download_failed": "Der Download ist fehlgeschlagen. Prüfen Sie die Internetverbindung und versuchen Sie es erneut.",
        "update_downloaded": "Das Update wurde geprüft und kann installiert werden.",
        "install_restart": "Installieren und neu starten",
        "update_restart_question": "OpsNest wird geschlossen, auf Version {version} aktualisiert und sofort neu gestartet. Lokale Daten bleiben gespeichert. Fortfahren?",
        "update_manual_development": "Automatische Updates funktionieren aus der installierten Windows-App. Die Entwicklungsversion wird nicht automatisch geändert.",
        "support_prompt": "Beschreiben Sie das Problem kurz (optional):",
    },
    "bg": {
        "trial_explainer": "Пробният период започва при регистрация на фирмата. Потвърждението на служебния e-mail само свързва сигурно плащането и поддръжката.",
        "per_month": "EUR / месец",
        "unlimited": "Неограничено",
        "card_limits": "{seats} потребителски места\n{projects} проекта\n{invoices} фактури / месец\n{pdfs} PDF импорта / месец",
        "not_registered": "Регистрирайте фирма, за да започне безплатният пробен период веднага.",
        "latest_update": "Използвате най-новата налична версия: {version}.",
        "update_available": "Налична е версия {version}. Да се отвори защитеният линк за инсталатор?",
        "installer_pending": "Налична е версия {version}, но линкът за инсталатора още не е публикуван.",
        "update_metadata_pending": "Налична е версия {version}, но нейната контролна сума за сигурност още не е публикувана. Да се отвори официалният линк за инсталатора?",
        "update_ready": "Налична е версия {version}. Изтеглянето остава в приложението, след което OpsNest стартира защитеното обновяване автоматично.",
        "update_safety_title": "Какво променя обновяването",
        "update_safety": "Подменят се само програмните файлове. Базата, фактурите, PDF файловете, приложенията и локалните данни остават непокътнати.",
        "update_waiting": "Готово за защитено изтегляне.",
        "download_install": "Изтегли и инсталирай",
        "update_downloading": "Изтегляне на обновяването: {percent}%.",
        "update_downloading_unknown": "Изтегляне на обновяването...",
        "update_integrity_failed": "Проверката за сигурност на обновяването не успя. Файлът не е инсталиран.",
        "update_download_failed": "Изтеглянето не успя. Проверете интернет връзката и опитайте отново.",
        "update_downloaded": "Обновяването е проверено и е готово за инсталиране.",
        "install_restart": "Инсталирай и рестартирай",
        "update_restart_question": "OpsNest ще се затвори, ще се обнови до версия {version} и веднага ще се стартира отново. Локалните данни остават запазени. Да продължи ли?",
        "update_manual_development": "Автоматичното обновяване работи от инсталираното Windows приложение. Версията за разработка не се променя автоматично.",
        "support_prompt": "Опишете накратко проблема (по избор):",
    },
    "ru": {
        "trial_explainer": "Пробный период начинается при регистрации компании. Подтверждение рабочего e-mail только безопасно связывает оплату и поддержку.",
        "per_month": "EUR / месяц",
        "unlimited": "Без ограничений",
        "card_limits": "{seats} мест пользователей\n{projects} проектов\n{invoices} счетов / месяц\n{pdfs} PDF-импортов / месяц",
        "not_registered": "Зарегистрируйте компанию, чтобы бесплатный пробный период начался сразу.",
        "latest_update": "Вы используете последнюю доступную версию: {version}.",
        "update_available": "Доступна версия {version}. Открыть защищенную ссылку на установщик?",
        "installer_pending": "Доступна версия {version}, но ссылка на установщик еще не опубликована.",
        "update_metadata_pending": "Доступна версия {version}, но ее контрольная сумма безопасности еще не опубликована. Открыть официальную ссылку на установщик?",
        "update_ready": "Доступна версия {version}. Загрузка остается в приложении, затем OpsNest автоматически запускает безопасное обновление.",
        "update_safety_title": "Что изменяет обновление",
        "update_safety": "Заменяются только файлы программы. База данных, счета, PDF, вложения и локальные данные остаются без изменений.",
        "update_waiting": "Готово к безопасной загрузке.",
        "download_install": "Скачать и установить",
        "update_downloading": "Загрузка обновления: {percent}%.",
        "update_downloading_unknown": "Загрузка обновления...",
        "update_integrity_failed": "Проверка безопасности обновления не удалась. Файл не установлен.",
        "update_download_failed": "Загрузка не удалась. Проверьте подключение к интернету и повторите попытку.",
        "update_downloaded": "Обновление проверено и готово к установке.",
        "install_restart": "Установить и перезапустить",
        "update_restart_question": "OpsNest закроется, обновится до версии {version} и сразу перезапустится. Локальные данные останутся сохранены. Продолжить?",
        "update_manual_development": "Автоматическое обновление работает из установленного приложения Windows. Версия разработки не изменяется автоматически.",
        "support_prompt": "Кратко опишите проблему (необязательно):",
    },
}


EXPORT_DIALOG_TRANSLATIONS = {
    "en": {
        "Jezik izveštaja": "Report language",
        "Applies only to this PDF and Excel export.": "Applies only to this PDF and Excel export.",
        "Važi samo za ovaj PDF i Excel izvoz.": "Applies only to this PDF and Excel export.",
        "Jezik samo ovog PDV PDF/Excel izvoza. Podrazumevano prati jezik programa.": "Language for this VAT PDF/Excel export only. By default it follows the application language.",
        "Jezik samo ovog paketa za knjigovođu. Podrazumevano prati jezik programa.": "Language for this accountant package only. By default it follows the application language.",
    },
    "de": {
        "Jezik izveštaja": "Berichtssprache",
        "Važi samo za ovaj PDF i Excel izvoz.": "Gilt nur für diesen PDF- und Excel-Export.",
        "Jezik samo ovog PDV PDF/Excel izvoza. Podrazumevano prati jezik programa.": "Sprache nur für diesen USt.-PDF-/Excel-Export. Standardmäßig gilt die Programmsprache.",
        "Jezik samo ovog paketa za knjigovođu. Podrazumevano prati jezik programa.": "Sprache nur für dieses Buchhaltungspaket. Standardmäßig gilt die Programmsprache.",
    },
    "bg": {
        "Jezik izveštaja": "Език на отчета",
        "Važi samo za ovaj PDF i Excel izvoz.": "Важи само за този PDF и Excel износ.",
        "Jezik samo ovog PDV PDF/Excel izvoza. Podrazumevano prati jezik programa.": "Език само за този ДДС PDF/Excel износ. По подразбиране следва езика на програмата.",
        "Jezik samo ovog paketa za knjigovođu. Podrazumevano prati jezik programa.": "Език само за този пакет за счетоводителя. По подразбиране следва езика на програмата.",
    },
    "ru": {
        "Jezik izveštaja": "Язык отчета",
        "Važi samo za ovaj PDF i Excel izvoz.": "Применяется только к этому PDF- и Excel-экспорту.",
        "Jezik samo ovog PDV PDF/Excel izvoza. Podrazumevano prati jezik programa.": "Язык только для этого PDF/Excel-экспорта НДС. По умолчанию используется язык программы.",
        "Jezik samo ovog paketa za knjigovođu. Podrazumevano prati jezik programa.": "Язык только для этого пакета для бухгалтера. По умолчанию используется язык программы.",
    },
}

# The finance centre is deliberately usable outside construction.  Keeping this
# compact vocabulary in the normal UI translation table also means a company
# can change its application language without changing stored accounting data.
FINANCE_UI_TRANSLATIONS = {
    "en": {
        "Finansijski centar firme": "Company finance centre", "Operativni pregled za vlasnika: obaveze, novac i plan. Nije zamena za lokalno zakonsko knjigovodstvo ili poresku prijavu.": "Owner's operational view of payables, cash and planning. It is not a replacement for local statutory accounting or tax filing.",
        "Novi dobavljač": "New supplier", "Nova obaveza": "New payable", "Ponavljajući trošak": "Recurring expense", "Račun / kasa": "Bank account / cash", "Kontni plan i dnevnik": "Chart of accounts & journal", "Zaključi period": "Close period", "Kreiraj dospele troškove": "Create due expenses", "Cash-flow:": "Cash flow:", "dana": "days", "Osveži": "Refresh",
        "Početno stanje računa": "Opening cash balance", "Otvorene obaveze": "Open payables", "Rezultat firme": "Company result", "Cash-flow izabranog horizonta": "Cash flow for selected horizon", "Obaveze dobavljačima": "Supplier payables", "Cash-flow prognoza": "Cash-flow forecast", "Finansijski audit": "Financial audit",
        "Rok": "Due date", "Dobavljač": "Supplier", "Broj": "Number", "Projekat": "Project", "Ukupno": "Total", "Plaćeno": "Paid", "Za plaćanje": "To pay", "Status": "Status", "Valuta": "Currency", "Početno": "Opening", "Očekivani prilivi": "Expected inflows", "Očekivani odlivi": "Expected outflows", "Procena na kraju": "Forecast closing", "Vreme": "Time", "Stavka": "Record", "Akcija": "Action", "Detalji": "Details", "Nema stavki": "No entries", "drugih valuta": "other currencies",
        "Dobavljač": "Supplier", "Obaveza dobavljača": "Supplier payable", "Ponavljajući trošak": "Recurring expense", "Račun ili kasa": "Bank account or cash", "Obračunski period": "Accounting period", "Naziv": "Name", "Matični / poreski broj": "Registration / tax ID", "Podrazumevani rok (dani)": "Default payment terms (days)", "Napomena": "Note", "Datum računa / početak": "Invoice date / start", "Rok / sledeće kreiranje": "Due date / next creation", "Iznos bez PDV": "Amount excluding VAT", "Kategorija": "Category", "Opis": "Description", "Ponavljanje (meseci)": "Repeat (months)", "Naziv računa/kase": "Account / cash name", "Početak perioda": "Period start", "Kraj perioda": "Period end", "Datumi: gggg-mm-dd. Zaključavanje sprečava nove operativne stavke sa tim datumom.": "Dates: yyyy-mm-dd. Closing prevents new operational entries with those dates.", "Otkaži": "Cancel",
        "Kontni plan i dvostavni dnevnik": "Chart of accounts & double-entry journal", "Kontni plan i radni dvostavni dnevnik": "Chart of accounts and working double-entry journal", "Ovo je kontrolni dnevnik za vlasnika i knjigovođu. Ne generiše zakonski kontni plan, bilans niti poresku prijavu bez lokalnog modula i potvrde knjigovođe.": "This is a control journal for the owner and accountant. It does not generate a statutory chart, financial statements or tax filing without a local module and accountant confirmation.", "Novo konto": "New account", "Nova dvostavna stavka": "New double-entry", "Šifra": "Code", "Vrsta": "Type", "Aktivno": "Active", "Dnevnik": "Journal", "Bruto bilans": "Trial balance", "Novo konto": "New account", "Šifra konta": "Account code", "Nova dvostavna stavka": "New double-entry", "Referenca": "Reference", "Iznos": "Amount", "Duguje konto": "Debit account", "Potražuje konto": "Credit account", "Isti iznos se knjiži na duguje i potražuje. Za korekciju proknjižene stavke unesite novu suprotnu stavku.": "The same amount is posted to debit and credit. To correct a posted entry, enter a new reversing entry.",
    },
    "de": {"Finansijski centar firme":"Finanzzentrale des Unternehmens","Novi dobavljač":"Neuer Lieferant","Nova obaveza":"Neue Verbindlichkeit","Ponavljajući trošak":"Wiederkehrende Ausgabe","Račun / kasa":"Bankkonto / Kasse","Kontni plan i dnevnik":"Kontenplan & Journal","Zaključi period":"Periode abschließen","Kreiraj dospele troškove":"Fällige Ausgaben erstellen","Osveži":"Aktualisieren","Otvorene obaveze":"Offene Verbindlichkeiten","Rezultat firme":"Unternehmensergebnis","Cash-flow prognoza":"Cashflow-Prognose","Obaveze dobavljačima":"Lieferantenverbindlichkeiten","Dobavljač":"Lieferant","Za plaćanje":"Zu zahlen","Valuta":"Währung","Otkaži":"Abbrechen","Sačuvaj":"Speichern"},
    "bg": {"Finansijski centar firme":"Финансов център на фирмата","Novi dobavljač":"Нов доставчик","Nova obaveza":"Ново задължение","Ponavljajući trošak":"Повтарящ се разход","Račun / kasa":"Банкова сметка / каса","Kontni plan i dnevnik":"Сметкоплан и дневник","Zaključi period":"Затвори период","Kreiraj dospele troškove":"Създай падежни разходи","Osveži":"Обнови","Otvorene obaveze":"Отворени задължения","Rezultat firme":"Резултат на фирмата","Cash-flow prognoza":"Прогноза за паричния поток","Obaveze dobavljačima":"Задължения към доставчици","Dobavljač":"Доставчик","Za plaćanje":"За плащане","Valuta":"Валута","Otkaži":"Отказ","Sačuvaj":"Запази"},
    "ru": {"Finansijski centar firme":"Финансовый центр компании","Novi dobavljač":"Новый поставщик","Nova obaveza":"Новое обязательство","Ponavljajući trošak":"Регулярный расход","Račun / kasa":"Банковский счёт / касса","Kontni plan i dnevnik":"План счетов и журнал","Zaključi period":"Закрыть период","Kreiraj dospele troškove":"Создать просроченные расходы","Osveži":"Обновить","Otvorene obaveze":"Открытые обязательства","Rezultat firme":"Результат компании","Cash-flow prognoza":"Прогноз движения денежных средств","Obaveze dobavljačima":"Обязательства перед поставщиками","Dobavljač":"Поставщик","Za plaćanje":"К оплате","Valuta":"Валюта","Otkaži":"Отмена","Sačuvaj":"Сохранить"},
}
for _finance_language, _finance_labels in FINANCE_UI_TRANSLATIONS.items():
    UI_TRANSLATIONS.setdefault(_finance_language, {}).update(_finance_labels)

# The invoice editor is the most frequently used accounting screen.  Keep its
# workflow selectors and template actions in the normal UI dictionary too.
INVOICE_EDITOR_UI_TRANSLATIONS = {
    "bg": {
        "Forma fakture": "Формуляр на фактура", "Upravljaj obrascima": "Управление на формуляри", "Otvori obrazac": "Отвори формуляра",
        "Originalni Delta obrazac je zaštićen; sopstveni obrazac se čuva kao posebna kopija.": "Оригиналният формуляр е защитен; вашият формуляр се съхранява като отделно копие.",
        "Vrsta računa": "Вид фактура", "Plaćeni avans": "Платен аванс", "Jezik dokumenta za izvoz": "Език на документа за износ",
        "Menja fiksne oznake na Excel/PDF fakturi; opisi stavki ostaju tačno kako su uneti.": "Превежда всички системни означения в Excel/PDF фактурата; въведените от вас свободни описания остават непроменени.",
        "Standardni račun": "Стандартна фактура", "Avansni račun": "Авансова фактура", "Završni račun": "Окончателна фактура",
        "Nacrt": "Чернова", "Na proveri": "За преглед", "Odobrena": "Одобрена", "Broj će biti dodeljen pri čuvanju": "Номерът ще бъде присвоен при записване",
        "Avans": "Аванс", "plaćeno": "платено", "Ugovorni avans": "Договорен аванс",
        "Avans se računa iz ugovora projekta; za završni račun izaberite plaćeni avans u kartici Detalji.": "Авансът се изчислява от договора на проекта; за окончателната фактура изберете платения аванс в раздел Детайли.",
        "Izaberite projekat sa vrednošću ugovora bez PDV-a i procentom avansa.": "Изберете проект със стойност на договора без ДДС и процент аванс.",
        "Ugovor bez PDV-a": "Договор без ДДС", "Avans bez PDV-a": "Аванс без ДДС", "OpsNest automatski pravi ugovornu avansnu stavku pri pregledu i čuvanju.": "OpsNest автоматично създава договорна авансова позиция при преглед и записване.",
        "Avans se obračunava iz ugovora projekta i ne unosi se kroz stavke rada, materijala ili ostalo.": "Авансът се изчислява от договора на проекта и не се въвежда като труд, материали или други позиции.",
    },
}
for _invoice_editor_language, _invoice_editor_labels in INVOICE_EDITOR_UI_TRANSLATIONS.items():
    UI_TRANSLATIONS.setdefault(_invoice_editor_language, {}).update(_invoice_editor_labels)

# Final Bulgarian UI audit.  These captions are produced by the main command
# bar, finance centre and archive screen, so keeping them here prevents a
# language switch from leaving a mixed Serbian/English business interface.
BULGARIAN_UI_AUDIT_TRANSLATIONS = {
    "Šabloni fakture": "Шаблони за фактури",
    "Odobrenja": "Одобрения",
    "Odobrenja ({count})": "Одобрения ({count})",
    "Firma i projekti": "Фирма и проекти",
    "Dashboard": "Табло",
    "Finansije": "Финанси",
    "Kupci": "Клиенти",
    "Backup": "Архив",
    "Smanji": "Минимизирай",
    "Uvećaj": "Увеличи",
    "Vrati veličinu": "Възстанови размера",
    "PDF / štampa": "PDF / печат",
    "Pregled fakture": "Преглед на фактура",
    "Pregled PDF / štampa": "PDF преглед / печат",
    "Priprema PDF-a fakture": "Подготовка на PDF фактура",
    "PDF iz originalnog Excel šablona nije moguće napraviti:": "PDF от оригиналния Excel шаблон не може да бъде създаден:",
    "Faktura": "Фактура",
    "Banka": "Банка",
    "Spremno za plaćanje": "Готово за плащане",
    "Operativni pregled za vlasnika: obaveze, novac i plan. Nije zamena za lokalno zakonsko knjigovodstvo ili poresku prijavu.": "Оперативен преглед за собственика: задължения, средства и план. Не замества местното законово счетоводство или данъчна декларация.",
    "Kontrola odobrenja: limit vlasnika nije podešen. Vlasnik može uključiti limit u Podacima firme.": "Контрол на одобренията: лимитът на собственика не е зададен. Собственикът може да го включи в Данни за фирмата.",
    "Otvori dokument": "Отвори документ",
    "Odobri obavezu": "Одобри задължението",
    "Odbij obavezu": "Отхвърли задължението",
    "Vrati na proveru": "Върни за преглед",
    "Komentari": "Коментари",
    "Mesečna kontrola": "Месечен контрол",
    "Izvezi audit": "Експортирай одита",
    "Plan plaćanja": "План за плащане",
    "P&L i PDV firme": "P&L и ДДС на фирмата",
    "Dodaj rashod / ulaznu fakturu": "Добави разход / входяща фактура",
    "Dodaj plaćanje": "Добави плащане",
    "Izdaj avans": "Издай аванс",
    "Pregled na pečalba": "Преглед на печалбата",
    "Dodaj izlaznu fakturu": "Добави изходяща фактура",
    "Budžet na projekta": "Бюджет на проекта",
    "Izvoz za računovođu": "Експорт за счетоводител",
    "Ponavljajuće fakture": "Повтарящи се фактури",
    "Dokumenti na projekta": "Документи на проекта",
    "Root folder": "Основна папка",
    "Database": "База данни",
    "Last backup": "Последно архивиране",
    "Backup now": "Архивирай сега",
    "Restore backup": "Възстанови архив",
    "Open root folder": "Отвори основната папка",
    "Open invoices folder": "Отвори папката с фактури",
    "Open backup folder": "Отвори папката с архиви",
    "Automatski backup se pravi pri čuvanju faktura i uplata. Ovaj ekran služi za ručni backup.": "Автоматичен архив се създава при записване на фактури и плащания. Този екран е за ръчно архивиране.",
}
UI_TRANSLATIONS.setdefault("bg", {}).update(BULGARIAN_UI_AUDIT_TRANSLATIONS)
_active_ui_language = "sr"
CLIPBOARD_HEADER_ALIASES = {
    "category": ("kategorija", "category", "tip", "vrsta", "vid", "vid smr", "vid radova"),
    "description": (
        "opis", "description", "stavka", "naziv", "usluga", "rad", "artikl", "artikal", "item", "product",
        "naimenovanie", "naimenovanie na stoka", "vid smr opisanie", "opisanie", "opisanie na smr",
    ),
    "unit": ("jm", "j.m.", "jedinica", "jedinica mere", "unit", "measure", "merka", "myarka", "merna edinica", "merna edinitsa"),
    "quantity": ("kolicina", "quantity", "qty", "kol", "broj", "broi", "kolichestvo"),
    "unit_price": (
        "cena", "price", "unit price", "unitprice", "unit cost", "jedinicna cena", "ed cena", "edinichna cena",
        "bez pdv", "cena bez pdv", "bez dds", "cena bez dds", "ed cena bez dds", "edinichna cena bez dds",
    ),
    "discount_percent": ("popust", "discount", "rabat", "otstapka", "otstapka procent", "discount percent"),
    "code_stage": ("kod", "etap", "stage", "oznaka", "reference", "pozicija", "poz", "kod etap", "kod etapa"),
}
CLIPBOARD_FIELD_LABELS = {
    "category": "Kategorija",
    "description": "Opis",
    "unit": "JM",
    "quantity": "Količina",
    "unit_price": "Cena bez PDV-a",
    "discount_percent": "Popust %",
    "code_stage": "Kod / etap",
}
CLIPBOARD_HEADER_FIELD_ORDER = ("description", "unit_price", "quantity", "unit", "discount_percent", "code_stage", "category")
ENTITY_CLIPBOARD_CONFIG = {
    "customer": {
        "order": ("name", "eik", "vat_number", "address", "contact_person", "phone", "email", "payment_term_days", "note"),
        "labels": {
            "name": "Naziv firme",
            "eik": "EIK / BULSTAT",
            "vat_number": "PDV broj",
            "address": "Adresa",
            "contact_person": "Odgovorno lice",
            "phone": "Telefon",
            "email": "E-mail",
            "payment_term_days": "Rok placanja",
            "note": "Napomena",
        },
        "aliases": {
            "name": ("naziv firme", "naziv", "firma", "kupac", "company", "customer"),
            "eik": ("eik", "bulstat", "eik bulstat"),
            "vat_number": ("pdv", "pdv broj", "dds", "vat", "vat broj"),
            "address": ("adresa", "sediste", "address"),
            "contact_person": ("odgovorno lice", "kontakt", "kontakt osoba", "contact person", "contact", "lice"),
            "phone": ("telefon", "telefon broj", "phone", "tel"),
            "email": ("e mail", "email", "mail"),
            "payment_term_days": ("rok placanja", "rok", "payment term", "payment days", "dani"),
            "note": ("napomena", "note", "notes", "beleska"),
        },
    },
    "project": {
        "order": ("name", "site_address", "contract_no", "protocol_no", "period_from", "period_to", "order_reference"),
        "labels": {
            "name": "Naziv projekta",
            "site_address": "Adresa gradilista",
            "contract_no": "Broj ugovora",
            "protocol_no": "Broj protokola / Akta 19",
            "period_from": "Period od",
            "period_to": "Period do",
            "order_reference": "Referenca",
        },
        "aliases": {
            "name": ("naziv projekta", "naziv", "projekat", "projekt", "objekat", "project"),
            "site_address": ("adresa gradilista", "gradiliste", "site address", "site", "adresa"),
            "contract_no": ("broj ugovora", "ugovor", "contract no", "contract"),
            "protocol_no": ("broj protokola", "akta 19", "akt 19", "protokol", "protocol"),
            "period_from": ("period od", "od", "from", "datum od"),
            "period_to": ("period do", "do", "to", "datum do"),
            "order_reference": ("poredjenja referenca", "referenca", "reference", "order reference", "nalog"),
        },
    },
}
CYRILLIC_TRANSLITERATION = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
        "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht",
        "ъ": "a", "ь": "y", "ю": "yu", "я": "ya",
    }
)
APP_ICON_FILE = ASSETS_DIR / "opsnest.ico"
APP_LOGO_FILE = ASSETS_DIR / "opsnest-app-mark.png"
APP_LOGO_SIZE = 84
OPSNEST_WEBSITE_URL = "https://opsnestone.com"
OPSNEST_CLOUD_API_URL = "https://api.opsnestone.com"
OPSNEST_PRICING_URL = f"{OPSNEST_WEBSITE_URL}/pricing"
OPSNEST_PAYPAL_CANCELLATION_URL = "https://www.paypal.com/myaccount/autopay/"
OPSNEST_APP_VERSION = "2.13.10"


def normalize_ui_language(value: Any) -> str:
    code = str(value or "sr").strip().lower()
    return code if code in UI_LANGUAGE_LABELS else "sr"


def set_active_ui_language(value: Any) -> str:
    global _active_ui_language
    _active_ui_language = normalize_ui_language(value)
    return _active_ui_language


def active_ui_language() -> str:
    return _active_ui_language


def language_label(value: Any) -> str:
    return UI_LANGUAGE_LABELS[normalize_ui_language(value)]


def language_code_from_label(value: Any) -> str:
    text = str(value or "").strip()
    for code, label in UI_LANGUAGE_LABELS.items():
        if text == label:
            return code
    return normalize_ui_language(text)


def invoice_document_language_code_from_label(value: Any) -> str:
    text = str(value or "").strip()
    for code, label in INVOICE_DOCUMENT_LANGUAGE_LABELS.items():
        if text == label:
            return code
    return text.lower() if text.lower() in INVOICE_DOCUMENT_LANGUAGE_LABELS else "sr"


COUNTRY_NAMES: dict[str, dict[str, str]] = {
    "AL": {"sr": "Albanija", "en": "Albania", "de": "Albanien", "bg": "Албания", "ru": "Албания"},
    "BA": {"sr": "Bosna i Hercegovina", "en": "Bosnia and Herzegovina", "de": "Bosnien und Herzegowina", "bg": "Босна и Херцеговина", "ru": "Босния и Герцеговина"},
    "BG": {"sr": "Bugarska", "en": "Bulgaria", "de": "Bulgarien", "bg": "България", "ru": "Болгария"},
    "DE": {"sr": "Nemačka", "en": "Germany", "de": "Deutschland", "bg": "Германия", "ru": "Германия"},
    "AT": {"sr": "Austrija", "en": "Austria", "de": "Österreich", "bg": "Австрия", "ru": "Австрия"},
    "BE": {"sr": "Belgija", "en": "Belgium", "de": "Belgien", "bg": "Белгия", "ru": "Бельгия"},
    "HR": {"sr": "Hrvatska", "en": "Croatia", "de": "Kroatien", "bg": "Хърватия", "ru": "Хорватия"},
    "CZ": {"sr": "Češka", "en": "Czechia", "de": "Tschechien", "bg": "Чехия", "ru": "Чехия"},
    "FR": {"sr": "Francuska", "en": "France", "de": "Frankreich", "bg": "Франция", "ru": "Франция"},
    "GR": {"sr": "Grčka", "en": "Greece", "de": "Griechenland", "bg": "Гърция", "ru": "Греция"},
    "HU": {"sr": "Mađarska", "en": "Hungary", "de": "Ungarn", "bg": "Унгария", "ru": "Венгрия"},
    "IE": {"sr": "Irska", "en": "Ireland", "de": "Irland", "bg": "Ирландия", "ru": "Ирландия"},
    "IT": {"sr": "Italija", "en": "Italy", "de": "Italien", "bg": "Италия", "ru": "Италия"},
    "ME": {"sr": "Crna Gora", "en": "Montenegro", "de": "Montenegro", "bg": "Черна гора", "ru": "Черногория"},
    "MK": {"sr": "Severna Makedonija", "en": "North Macedonia", "de": "Nordmazedonien", "bg": "Северна Македония", "ru": "Северная Македония"},
    "NL": {"sr": "Holandija", "en": "Netherlands", "de": "Niederlande", "bg": "Нидерландия", "ru": "Нидерланды"},
    "PL": {"sr": "Poljska", "en": "Poland", "de": "Polen", "bg": "Полша", "ru": "Польша"},
    "PT": {"sr": "Portugal", "en": "Portugal", "de": "Portugal", "bg": "Португалия", "ru": "Португалия"},
    "RO": {"sr": "Rumunija", "en": "Romania", "de": "Rumänien", "bg": "Румъния", "ru": "Румыния"},
    "RS": {"sr": "Srbija", "en": "Serbia", "de": "Serbien", "bg": "Сърбия", "ru": "Сербия"},
    "SK": {"sr": "Slovačka", "en": "Slovakia", "de": "Slowakei", "bg": "Словакия", "ru": "Словакия"},
    "SI": {"sr": "Slovenija", "en": "Slovenia", "de": "Slowenien", "bg": "Словения", "ru": "Словения"},
    "ES": {"sr": "Španija", "en": "Spain", "de": "Spanien", "bg": "Испания", "ru": "Испания"},
    "GB": {"sr": "Ujedinjeno Kraljevstvo", "en": "United Kingdom", "de": "Vereinigtes Königreich", "bg": "Обединеното кралство", "ru": "Великобритания"},
    "XK": {"sr": "Kosovo", "en": "Kosovo", "de": "Kosovo", "bg": "Косово", "ru": "Косово"},
    "OTHER": {"sr": "Druga država", "en": "Other country", "de": "Anderes Land", "bg": "Друга държава", "ru": "Другая страна"},
}

BUSINESS_PROFILE_LABELS = {
    "construction": "Građevina i projektni rad",
    "general": "Opšta delatnost / usluge / trgovina",
    "professional_services": "Stručne i poslovne usluge",
    "retail_trade": "Trgovina i maloprodaja",
    "hospitality": "Ugostiteljstvo i turizam",
    "manufacturing": "Proizvodnja",
    "digital_creative": "Digitalne i kreativne usluge",
    "nonprofit": "Udruženje / neprofitna organizacija",
}

VAT_REGIME_LABELS = {
    "standard": "Standardni PDV obveznik",
    "exempt": "Oslobođen PDV-a",
    "reverse_charge": "Prenos poreske obaveze (reverse charge)",
    "out_of_scope": "Van sistema PDV-a",
}

EINVOICE_ROUTE_LABELS = {
    "automatic": "Automatski prema državi (preporučeno)",
    "structured_ubl": "Strukturirani UBL / EN 16931 dokument",
    "external_portal": "Spoljni portal ili provajder firme",
}


def business_profile_label(value: Any) -> str:
    return BUSINESS_PROFILE_LABELS.get(str(value or "").strip().lower(), BUSINESS_PROFILE_LABELS["general"])


def business_profile_code_from_label(value: Any) -> str:
    text = str(value or "").strip()
    for code, label in BUSINESS_PROFILE_LABELS.items():
        if text == label:
            return code
    return text if text in BUSINESS_PROFILE_LABELS else "general"


def vat_regime_label(value: Any) -> str:
    return VAT_REGIME_LABELS.get(str(value or "").strip().lower(), VAT_REGIME_LABELS["standard"])


def vat_regime_code_from_label(value: Any) -> str:
    text = str(value or "").strip()
    for code, label in VAT_REGIME_LABELS.items():
        if text == label:
            return code
    return text if text in VAT_REGIME_LABELS else "standard"


def einvoice_route_label(value: Any) -> str:
    return EINVOICE_ROUTE_LABELS.get(str(value or "").strip().lower(), EINVOICE_ROUTE_LABELS["automatic"])


def einvoice_route_code_from_label(value: Any) -> str:
    text = str(value or "").strip()
    for code, label in EINVOICE_ROUTE_LABELS.items():
        if text == label:
            return code
    return text if text in EINVOICE_ROUTE_LABELS else "automatic"


def country_option_label(value: Any, language: str | None = None) -> str:
    code = normalize_country_code(value)
    lang = normalize_ui_language(language or active_ui_language())
    names = COUNTRY_NAMES.get(code, COUNTRY_NAMES["OTHER"])
    return f"{code} - {names.get(lang, names['en'])}"


def country_option_values(language: str | None = None) -> list[str]:
    return [country_option_label(code, language) for code in COUNTRY_VAT_DEFAULTS]


def country_code_from_option(value: Any) -> str:
    return normalize_country_code(str(value or "").split("-", 1)[0].strip())


def tr(text: str, language: str | None = None) -> str:
    code = normalize_ui_language(language or _active_ui_language)
    return UI_TRANSLATIONS.get(code, {}).get(text, EXPORT_DIALOG_TRANSLATIONS.get(code, {}).get(text, text))


def canonical_ui_text(text: str, language: str) -> str:
    """Recover the original Serbian key when a widget was created after a language switch."""
    code = normalize_ui_language(language)
    if code == "sr":
        return text
    for source, translated in UI_TRANSLATIONS.get(code, {}).items():
        if translated == text:
            return source
    for source, translated in EXPORT_DIALOG_TRANSLATIONS.get(code, {}).items():
        if translated == text:
            return source
    return text


def localized_status_label(status_code: Any, language: str | None = None) -> str:
    """Display a workflow status in the active UI language without changing its stored code."""
    return tr(status_label(str(status_code or "draft")), language)


def status_code_from_display(value: Any, language: str | None = None) -> str:
    """Translate a selected status caption back to the stable database status code."""
    source = canonical_ui_text(str(value or "").strip(), normalize_ui_language(language or active_ui_language()))
    for code, label in STATUS_LABELS.items():
        if source == label:
            return code
    return ""


def subscription_copy(key: str, language: str | None = None, **values: Any) -> str:
    code = normalize_ui_language(language or _active_ui_language)
    text = SUBSCRIPTION_COPY.get(code, SUBSCRIPTION_COPY["sr"]).get(key, SUBSCRIPTION_COPY["sr"].get(key, key))
    return text.format(**values)


def plan_dialog_copy(key: str, language: str | None = None, **values: Any) -> str:
    code = normalize_ui_language(language or _active_ui_language)
    text = PLAN_DIALOG_COPY.get(code, PLAN_DIALOG_COPY["sr"]).get(key, PLAN_DIALOG_COPY["sr"].get(key, key))
    return text.format(**values)


def version_key(value: Any) -> tuple[int, ...]:
    """Compare desktop versions numerically, including releases such as 2.8.10."""
    parts: list[int] = []
    for part in str(value or "").strip().split("."):
        digits = "".join(char for char in part if char.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts or [0])


def installed_app_dir() -> Path | None:
    """Return the program folder only for a packaged Windows installation."""
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    return executable.parent if executable.suffix.lower() == ".exe" else None


def update_cache_dir() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return local_app_data / APP_NAME / "Updates"


def localize_widget_tree(root: tk.Misc, language: str | None = None) -> None:
    """Translate labels, buttons, tabs, and table headings from their original Serbian text."""
    code = normalize_ui_language(language or _active_ui_language)

    # Dialog titles are outside the normal widget tree, so translate them here
    # together with labels and headings.
    if isinstance(root, (tk.Tk, tk.Toplevel)):
        try:
            current_title = str(root.title())
            source_title = getattr(root, "_opsnest_source_title", canonical_ui_text(current_title, code))
            if not hasattr(root, "_opsnest_source_title"):
                setattr(root, "_opsnest_source_title", source_title)
            if source_title:
                root.title(tr(source_title, code))
        except tk.TclError:
            pass

    def localize_widget(widget: tk.Misc) -> None:
        try:
            if "text" in widget.keys():
                current = str(widget.cget("text"))
                source = getattr(widget, "_opsnest_source_text", canonical_ui_text(current, code))
                if not hasattr(widget, "_opsnest_source_text"):
                    setattr(widget, "_opsnest_source_text", source)
                if source:
                    widget.configure(text=tr(source, code))
        except tk.TclError:
            pass

        if isinstance(widget, ttk.Notebook):
            sources = getattr(widget, "_opsnest_tab_sources", {})
            for tab_id in widget.tabs():
                current = str(widget.tab(tab_id, "text"))
                source = sources.get(tab_id, canonical_ui_text(current, code))
                sources[tab_id] = source
                widget.tab(tab_id, text=tr(source, code))
            setattr(widget, "_opsnest_tab_sources", sources)

        if isinstance(widget, ttk.Treeview):
            sources = getattr(widget, "_opsnest_heading_sources", {})
            for column in widget["columns"]:
                current = str(widget.heading(column, "text"))
                source = sources.get(column, canonical_ui_text(current, code))
                sources[column] = source
                widget.heading(column, text=tr(source, code))
            setattr(widget, "_opsnest_heading_sources", sources)

        if isinstance(widget, ttk.Combobox):
            # Values inside workflow selectors are visible commands too. Keep
            # the original Serbian values once, then translate the display on
            # every language refresh without changing the stored business code.
            source_values = getattr(widget, "_opsnest_value_sources", None)
            if source_values is None:
                source_values = tuple(canonical_ui_text(str(value), code) for value in widget.cget("values"))
                setattr(widget, "_opsnest_value_sources", source_values)
            widget.configure(values=tuple(tr(value, code) for value in source_values))
            current = str(widget.get() or "")
            source_current = canonical_ui_text(current, code)
            if source_current in source_values:
                widget.set(tr(source_current, code))

        for child in widget.winfo_children():
            localize_widget(child)

    localize_widget(root)
    # Translated labels can become wider after a dialog has chosen its default
    # geometry. Re-measure on idle so fields and bottom actions stay visible.
    top_level = root.winfo_toplevel()
    if isinstance(top_level, tk.Toplevel):
        top_level.after_idle(lambda: fit_dialog_to_content(top_level))


def open_path(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606
    else:
        import subprocess

        subprocess.Popen(["xdg-open", str(path)])


def fmt_money(value: Any, currency: str = DEFAULT_CURRENCY) -> str:
    return format_currency(value, currency)


def display_date(value: Any) -> str:
    return format_date(value)


def format_file_size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def due_date_from(issue_date: Any, days: int) -> str:
    parsed = parse_date(issue_date) or date.today()
    return (parsed + timedelta(days=days)).isoformat()


def ensure_text(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_clipboard_token(value: Any) -> str:
    text = "" if value is None else str(value).lower().translate(CYRILLIC_TRANSLITERATION)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text)


CLIPBOARD_CATEGORY_TOKENS = {normalize_clipboard_token(value) for value in CATEGORY_OPTIONS}


def parse_clipboard_number(value: Any) -> float | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    text = text.replace("\xa0", "").replace(" ", "")
    text = re.sub(r"[^\d,.\-+]", "", text)
    if not text or text in {"+", "-", ".", ",", "+.", "-.", "+,", "-,"}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def format_clipboard_number(value: Any) -> str:
    parsed = parse_clipboard_number(value)
    if parsed is None:
        return ""
    return format(parsed, ".15g")


def format_clipboard_percent(value: Any) -> str:
    raw = "" if value is None else str(value).strip()
    parsed = parse_clipboard_number(raw)
    if parsed is None:
        return ""
    if "%" not in raw and 0 < parsed <= 1:
        parsed *= 100
    return format(parsed, ".15g")


def clipboard_rows_from_text(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        delimiter = "\t" if "\t" in line else ";" if ";" in line else "|" if "|" in line else ","
        values = [cell.strip() for cell in next(csv.reader([line], delimiter=delimiter))]
        if not any(values):
            continue
        if len(values) >= 8 and values[0].isdigit():
            values = values[1:]
        rows.append(values)
    return rows


def _clipboard_header_match_score(field: str, token: str) -> int:
    if not token:
        return 0
    if field == "unit_price" and any(blocked in token for blocked in ("ukupno", "obshto", "iznos", "stoimost", "saddv", "sddv", "withvat")):
        return 0
    best_score = 0
    for alias in CLIPBOARD_HEADER_ALIASES[field]:
        alias_token = normalize_clipboard_token(alias)
        if not alias_token:
            continue
        if token == alias_token:
            best_score = max(best_score, 10000 + len(alias_token))
        elif token.startswith(alias_token) or token.endswith(alias_token):
            best_score = max(best_score, 5000 + len(alias_token))
        elif alias_token in token:
            best_score = max(best_score, 2000 + len(alias_token))
    return best_score


def clipboard_header_map_from_row(values: list[str]) -> dict[str, int] | None:
    tokens = [normalize_clipboard_token(value) for value in values]
    header_map: dict[str, int] = {}
    used_columns: set[int] = set()
    for field in CLIPBOARD_HEADER_FIELD_ORDER:
        candidates = [
            (_clipboard_header_match_score(field, token), idx)
            for idx, token in enumerate(tokens)
            if idx not in used_columns
        ]
        score, idx = max(candidates, default=(0, -1))
        if score:
            header_map[field] = idx
            used_columns.add(idx)
    return header_map if len(header_map) >= 2 else None


def clipboard_mapping_summary(header_map: dict[str, int]) -> str:
    if not header_map:
        return "Nisu prepoznata zaglavlja; korišćen je standardni redosled kolona."
    fields = [
        f"{CLIPBOARD_FIELD_LABELS[field]} = kolona {header_map[field] + 1}"
        for field in CLIPBOARD_HEADER_FIELD_ORDER
        if field in header_map
    ]
    return " | ".join(fields)


def clipboard_finalize_item_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    payload["category"] = str(payload.get("category", "")).strip() or CATEGORY_OPTIONS[0]
    payload["description"] = str(payload.get("description", "")).strip()
    payload["unit"] = str(payload.get("unit", "")).strip() or UNIT_OPTIONS[0]
    payload["quantity"] = format_clipboard_number(payload.get("quantity", ""))
    payload["unit_price"] = format_clipboard_number(payload.get("unit_price", ""))
    payload["discount_percent"] = format_clipboard_percent(payload.get("discount_percent", ""))
    payload["code_stage"] = str(payload.get("code_stage", "")).strip()
    if not payload["description"]:
        return None
    if not payload["quantity"] or not payload["unit_price"]:
        return None
    if not payload["discount_percent"]:
        payload["discount_percent"] = "0"
    return payload


def clipboard_payload_from_values(values: list[str], header_map: dict[str, int] | None = None) -> dict[str, Any] | None:
    cleaned = [cell.strip() for cell in values]
    if not any(cleaned):
        return None
    if header_map:
        payload = {
            "category": cleaned[header_map["category"]] if "category" in header_map and header_map["category"] < len(cleaned) else "",
            "description": cleaned[header_map["description"]] if "description" in header_map and header_map["description"] < len(cleaned) else "",
            "unit": cleaned[header_map["unit"]] if "unit" in header_map and header_map["unit"] < len(cleaned) else "",
            "quantity": cleaned[header_map["quantity"]] if "quantity" in header_map and header_map["quantity"] < len(cleaned) else "",
            "unit_price": cleaned[header_map["unit_price"]] if "unit_price" in header_map and header_map["unit_price"] < len(cleaned) else "",
            "discount_percent": cleaned[header_map["discount_percent"]] if "discount_percent" in header_map and header_map["discount_percent"] < len(cleaned) else "0",
            "code_stage": cleaned[header_map["code_stage"]] if "code_stage" in header_map and header_map["code_stage"] < len(cleaned) else "",
        }
        return clipboard_finalize_item_payload(payload)
    if cleaned and cleaned[0].isdigit() and len(cleaned) >= 8:
        cleaned = cleaned[1:]
    if len(cleaned) >= 10:
        return clipboard_finalize_item_payload(
            {
                "category": cleaned[0],
                "description": cleaned[1],
                "unit": cleaned[2],
                "quantity": cleaned[3],
                "unit_price": cleaned[4],
                "discount_percent": cleaned[5],
                "code_stage": cleaned[9],
            }
        )
    if len(cleaned) >= 7:
        if normalize_clipboard_token(cleaned[0]) in CLIPBOARD_CATEGORY_TOKENS:
            return clipboard_finalize_item_payload(
                {
                    "category": cleaned[0],
                    "description": cleaned[1],
                    "unit": cleaned[2],
                    "quantity": cleaned[3],
                    "unit_price": cleaned[4],
                    "discount_percent": cleaned[5],
                    "code_stage": cleaned[6],
                }
            )
        return clipboard_finalize_item_payload(
            {
                "category": CATEGORY_OPTIONS[0],
                "description": cleaned[0],
                "unit": cleaned[1],
                "quantity": cleaned[2],
                "unit_price": cleaned[3],
                "discount_percent": cleaned[4],
                "code_stage": cleaned[5] if len(cleaned) > 5 else "",
            }
        )
    if len(cleaned) >= 6:
        if normalize_clipboard_token(cleaned[0]) in CLIPBOARD_CATEGORY_TOKENS:
            return clipboard_finalize_item_payload(
                {
                    "category": cleaned[0],
                    "description": cleaned[1],
                    "unit": cleaned[2],
                    "quantity": cleaned[3],
                    "unit_price": cleaned[4],
                    "discount_percent": cleaned[5],
                    "code_stage": "",
                }
            )
        return clipboard_finalize_item_payload(
            {
                "category": CATEGORY_OPTIONS[0],
                "description": cleaned[0],
                "unit": cleaned[1],
                "quantity": cleaned[2],
                "unit_price": cleaned[3],
                "discount_percent": cleaned[4],
                "code_stage": cleaned[5],
            }
        )
    return None


def clipboard_payloads_from_text(text: str) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    rows = clipboard_rows_from_text(text)
    if not rows:
        return [], 0, {}
    header_map = clipboard_header_map_from_row(rows[0])
    if header_map:
        rows = rows[1:]
    imported_payloads: list[dict[str, Any]] = []
    skipped_rows = 0
    for row in rows:
        payload = clipboard_payload_from_values(row, header_map=header_map)
        if not payload:
            skipped_rows += 1
            continue
        imported_payloads.append(payload)
    return imported_payloads, skipped_rows, header_map or {}


def _entity_header_match_score(aliases: tuple[str, ...], token: str) -> int:
    if not token:
        return 0
    best_score = 0
    for alias in aliases:
        alias_token = normalize_clipboard_token(alias)
        if not alias_token:
            continue
        if token == alias_token:
            best_score = max(best_score, 10000 + len(alias_token))
        elif token.startswith(alias_token) or token.endswith(alias_token):
            best_score = max(best_score, 5000 + len(alias_token))
        elif alias_token in token:
            best_score = max(best_score, 2000 + len(alias_token))
    return best_score


def entity_clipboard_payload_from_text(text: str, entity_type: str) -> tuple[dict[str, str], dict[str, int], str]:
    """Read an Excel row with headers, label/value pairs, or the documented field order."""
    config = ENTITY_CLIPBOARD_CONFIG[entity_type]
    rows = clipboard_rows_from_text(text)
    if not rows:
        return {}, {}, ""

    field_order: tuple[str, ...] = config["order"]
    aliases: dict[str, tuple[str, ...]] = config["aliases"]
    labels: dict[str, str] = config["labels"]

    label_map = {
        normalize_clipboard_token(alias): field
        for field in field_order
        for alias in aliases[field]
    }
    pair_payload: dict[str, str] = {}
    for row in rows:
        if len(row) < 2:
            continue
        field = label_map.get(normalize_clipboard_token(row[0]))
        if field and row[1].strip():
            pair_payload[field] = row[1].strip()
    # Two-column label/value data is a form export, not an Excel header row.
    if len(pair_payload) >= 2 and all(len(row) <= 2 for row in rows):
        return pair_payload, {}, "polje/vrednost"

    header_map: dict[str, int] = {}
    used_columns: set[int] = set()
    for field in field_order:
        candidates = [
            (_entity_header_match_score(aliases[field], normalize_clipboard_token(value)), index)
            for index, value in enumerate(rows[0])
            if index not in used_columns
        ]
        score, index = max(candidates, default=(0, -1))
        if score:
            header_map[field] = index
            used_columns.add(index)
    if header_map and len(rows) >= 2:
        values = rows[1]
        payload = {
            field: values[index].strip()
            for field, index in header_map.items()
            if index < len(values) and values[index].strip()
        }
        if payload:
            return payload, header_map, "zaglavlja"

    if pair_payload:
        return pair_payload, {}, "polje/vrednost"

    values = rows[0]
    if len(values) >= 2:
        payload = {
            field: values[index].strip()
            for index, field in enumerate(field_order)
            if index < len(values) and values[index].strip()
        }
        if payload:
            return payload, {}, "redosled"

    return {}, {}, ""


def entity_clipboard_mapping_summary(entity_type: str, header_map: dict[str, int], source: str) -> str:
    config = ENTITY_CLIPBOARD_CONFIG[entity_type]
    labels: dict[str, str] = config["labels"]
    if source == "zaglavlja":
        return " | ".join(f"{labels[field]} = kolona {index + 1}" for field, index in header_map.items())
    if source == "polje/vrednost":
        return "Prepoznat je raspored: naziv polja | vrednost."
    return "Korišćen je redosled polja prikazan u formi."


def desktop_work_area(win: tk.Toplevel | tk.Tk) -> tuple[int, int]:
    """Return the physical usable desktop size when Windows DPI scaling is active."""
    if sys.platform.startswith("win"):
        class Rect(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = Rect()
        try:
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 0 and height > 0:
                    return width, height
        except (AttributeError, OSError):
            pass
    return win.winfo_screenwidth(), win.winfo_screenheight()


def center_window(win: tk.Toplevel | tk.Tk, width: int, height: int) -> None:
    """Size dialogs from their actual contents before placing them on screen."""
    win.update_idletasks()
    screen_width, screen_height = desktop_work_area(win)
    # Geometry is the client area. Account for the title bar/border and for
    # translated labels that may be wider than the original Serbian text.
    requested_width = win.winfo_reqwidth() + 18
    requested_height = win.winfo_reqheight() + 22
    max_width = max(320, screen_width - 24)
    max_height = max(260, screen_height - 48)
    width = min(max(width, requested_width), max_width)
    height = min(max(height, requested_height), max_height)
    try:
        win.maxsize(max_width, max_height)
    except tk.TclError:
        pass
    x = max(12, (screen_width - width) // 2)
    y = max(12, (screen_height - height) // 3)
    win.geometry(f"{width}x{height}+{x}+{y}")


def fit_dialog_to_content(win: tk.Toplevel) -> None:
    """Re-fit a normal dialog after dynamic text or a language has been applied."""
    if not win.winfo_exists():
        return
    try:
        if win.state() != "normal":
            return
    except tk.TclError:
        return
    win.update_idletasks()
    center_window(win, max(1, win.winfo_width()), max(1, win.winfo_height()))


def maximize_large_window(win: tk.Toplevel | tk.Tk, *, minimum_width: int, minimum_height: int) -> None:
    """Maximize data-heavy dialogs while keeping their minimum size screen-safe."""
    screen_width, screen_height = desktop_work_area(win)
    max_width = max(320, screen_width - 24)
    max_height = max(260, screen_height - 48)
    win.minsize(
        min(minimum_width, max_width),
        min(minimum_height, max_height),
    )
    try:
        win.maxsize(max_width, max_height)
    except tk.TclError:
        pass
    try:
        win.state("zoomed")
    except tk.TclError:
        center_window(win, screen_width - 24, screen_height - 48)


def enable_high_dpi() -> None:
    """Let Windows draw Tk sharply instead of bitmap-scaling the whole app."""
    if not sys.platform.startswith("win"):
        return
    try:
        # Per-monitor V2 keeps the application sharp when it moves between screens.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def preferred_ui_font(root: tk.Misc) -> str:
    try:
        families = set(root.tk.call("font", "families"))
    except tk.TclError:
        families = set()
    for candidate in ("Segoe UI Variable Text", "Segoe UI Variable", "Aptos", "Segoe UI", "Arial"):
        if candidate in families:
            return candidate
    return "Segoe UI"


def load_logo_photo(path: Path, size: int = APP_LOGO_SIZE) -> ImageTk.PhotoImage | None:
    if not path.exists():
        return None
    try:
        image = Image.open(path).convert("RGBA")
        contained = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        x = (size - contained.width) // 2
        y = (size - contained.height) // 2
        canvas.alpha_composite(contained, (x, y))
        return ImageTk.PhotoImage(canvas)
    except Exception:
        return None


def configure_style(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    try:
        dpi_scale = float(root.winfo_fpixels("1i")) / 96.0
        root.tk.call("tk", "scaling", max(1.0, min(dpi_scale, 1.5)))
    except tk.TclError:
        pass
    ui_font = preferred_ui_font(root)
    root.option_add("*Font", (ui_font, 10))
    root.configure(background=BG)
    style.configure(".", background=BG, foreground=TEXT, font=(ui_font, 10))
    style.configure("App.TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("Hero.TFrame", background=HEADER_BG)
    style.configure("HeroInset.TFrame", background=HEADER_BG_2)
    style.configure("Header.TFrame", background=HEADER_BG)
    style.configure("Hero.TLabel", background=HEADER_BG)
    style.configure("Header.TLabel", background=HEADER_BG, foreground="#164B44", font=(ui_font, 13, "bold"))
    style.configure("HeroTitle.TLabel", background=HEADER_BG, foreground="#063D36", font=(ui_font, 26, "bold"))
    style.configure("HeroSub.TLabel", background=HEADER_BG, foreground="#285F57", font=(ui_font, 11, "bold"))
    style.configure("HeroLink.TLabel", background=HEADER_BG, foreground=ACCENT_2, font=(ui_font, 13, "bold underline"))
    style.configure("HeroMeta.TLabel", background=HEADER_BG, foreground="#5F7F79", font=(ui_font, 9))
    style.configure("Chip.TLabel", background=HEADER_BG_2, foreground="#245B53", font=(ui_font, 9, "bold"), padding=(8, 3))
    style.configure("Help.TLabel", background=BG, foreground=MUTED, font=(ui_font, 9))
    style.configure("Link.TLabel", background=PANEL, foreground=ACCENT, font=(ui_font, 10, "underline"))
    style.configure("Section.TLabel", background=BG, foreground=ACCENT, font=(ui_font, 13, "bold"))
    style.configure("CardTitle.TLabel", background=PANEL, foreground=MUTED, font=(ui_font, 9))
    style.configure("CardValue.TLabel", background=PANEL, foreground=TEXT, font=(ui_font, 18, "bold"))
    style.configure("AccessBrand.TLabel", background=PANEL, foreground="#063D36", font=(ui_font, 28, "bold"))
    style.configure("AccessSub.TLabel", background=PANEL, foreground="#285F57", font=(ui_font, 11, "bold"))
    style.configure("AccessHelp.TLabel", background=PANEL, foreground=MUTED, font=(ui_font, 10))
    style.configure("Value.TLabel", background=BG, foreground=TEXT, font=(ui_font, 11, "bold"))
    style.configure("CompanyName.TLabel", background=BG, foreground="#123F39", font=(ui_font, 22, "bold"))
    style.configure("CompanyInfo.TLabel", background=BG, foreground="#365A63", font=(ui_font, 11))
    style.configure("ProjectDashboard.TLabel", background=BG, foreground=ACCENT, font=(ui_font, 14, "bold"))
    style.configure("Field.TLabel", background=BG, foreground=MUTED, font=(ui_font, 9))
    style.configure("Primary.TButton", background=ACCENT, foreground="white", padding=(12, 8), font=(ui_font, 10, "bold"))
    style.map("Primary.TButton", background=[("active", ACCENT_2), ("pressed", ACCENT_2)], foreground=[("disabled", "#CBD5E1")])
    style.configure("Header.TButton", background="#FFFFFF", foreground="#245B53", padding=(10, 6), font=(ui_font, 9, "bold"))
    style.map("Header.TButton", background=[("active", "#F6FCFA"), ("pressed", "#D3ECE7")], foreground=[("disabled", "#94A3B8")])
    style.configure("HeaderPrimary.TButton", background=ACCENT, foreground="white", padding=(12, 8), font=(ui_font, 9, "bold"))
    style.map("HeaderPrimary.TButton", background=[("active", ACCENT_2), ("pressed", ACCENT_2)])
    style.configure("Total.TFrame", background="#E6F5F1")
    style.configure("TotalKey.TLabel", background="#E6F5F1", foreground="#47656B", font=(ui_font, 8, "bold"))
    style.configure("TotalValue.TLabel", background="#E6F5F1", foreground=TEXT, font=(ui_font, 11, "bold"))
    style.configure("TotalDue.TLabel", background="#E6F5F1", foreground=ACCENT_2, font=(ui_font, 12, "bold"))
    style.configure("TButton", padding=(10, 7), font=(ui_font, 10))
    style.configure("TEntry", padding=(8, 6))
    style.configure("TCombobox", padding=(6, 5))
    style.configure("Modern.TEntry", padding=(8, 6))
    style.configure("Modern.TCombobox", padding=(6, 5))
    style.configure("Treeview", rowheight=30, fieldbackground="#FCFDFE", background="#FCFDFE", bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, borderwidth=0)
    style.configure("Treeview.Heading", font=(ui_font, 9, "bold"), background=SOFT, foreground=TEXT, padding=(9, 9))
    style.map("Treeview", background=[("selected", "#D6F3EB")], foreground=[("selected", TEXT)])
    style.configure("TNotebook", background=BG, tabmargins=(10, 8, 10, 0))
    style.configure("TNotebook.Tab", padding=(16, 10), font=(ui_font, 10, "bold"))
    style.map("TNotebook.Tab", background=[("selected", PANEL), ("active", SOFT)], foreground=[("selected", ACCENT), ("active", TEXT)])
    style.configure("TLabelframe", background=BG, borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=(ui_font, 10, "bold"))
    style.configure("TCheckbutton", background=BG, foreground=TEXT)
    style.configure("TRadiobutton", background=BG, foreground=TEXT)


def add_field(
    parent: ttk.Frame,
    row: int,
    col: int,
    label: str,
    var: tk.StringVar,
    width: int = 22,
    readonly: bool = False,
    show: str | None = None,
) -> ttk.Entry:
    ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=col, sticky="w", padx=(0, 6), pady=3)
    entry = ttk.Entry(parent, textvariable=var, width=width, show=show, style="Modern.TEntry")
    if readonly:
        entry.state(["readonly"])
    entry.grid(row=row, column=col + 1, sticky="ew", padx=(0, 14), pady=3)
    return entry


def add_combo(parent: ttk.Frame, row: int, col: int, label: str, var: tk.StringVar, values: list[str], width: int = 22) -> ttk.Combobox:
    ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=col, sticky="w", padx=(0, 6), pady=3)
    combo = ttk.Combobox(parent, textvariable=var, values=values, width=width, state="readonly", style="Modern.TCombobox")
    combo.grid(row=row, column=col + 1, sticky="ew", padx=(0, 14), pady=3)
    return combo


def add_text(parent: ttk.Frame, row: int, col: int, label: str, text: tk.Text, height: int = 4) -> tk.Text:
    ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=col, sticky="nw", padx=(0, 6), pady=3)
    text.grid(row=row, column=col + 1, sticky="nsew", padx=(0, 14), pady=3)
    text.configure(height=height, wrap="word", background="white", foreground=TEXT, insertbackground=TEXT, relief="solid", borderwidth=1, highlightthickness=1, highlightbackground=LINE)
    return text


def setup_treeview_tree(tree: ttk.Treeview) -> None:
    tree.tag_configure("row_even", background="#F8FBFD")
    tree.tag_configure("row_odd", background="#FFFFFF")


def tree_row_tag(index: int) -> str:
    return "row_even" if index % 2 == 0 else "row_odd"


class Tooltip:
    """Small delayed help bubble for actions that accept clipboard data."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 450) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.after_id: str | None = None
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, event: tk.Event | None = None) -> None:
        self._hide()
        self.after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self) -> None:
        self.after_id = None
        if not self.widget.winfo_exists() or not self.text:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.configure(background="#123F39")
        label = tk.Label(
            self.window,
            text=self.text,
            justify="left",
            wraplength=310,
            background="#123F39",
            foreground="white",
            padx=9,
            pady=6,
            font=(preferred_ui_font(self.widget), 9),
        )
        label.pack()
        self.window.update_idletasks()
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.window.wm_geometry(f"+{x}+{y}")

    def _hide(self, event: tk.Event | None = None) -> None:
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


def add_tooltip(widget: tk.Widget, text: str) -> Tooltip:
    tooltip = Tooltip(widget, text)
    # Keep the helper alive for the full lifetime of the widget.
    setattr(widget, "_opsnest_tooltip", tooltip)
    return tooltip


class ScrollableFrame(ttk.Frame):
    def __init__(self, master: tk.Widget, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, background=BG, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="App.TFrame")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind("<Configure>", self._resize_inner)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _resize_inner(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)


def open_original_invoice_template() -> None:
    """Open a read-only copy so the packaged master workbook cannot be changed."""
    if not TEMPLATE_XLSX.exists():
        messagebox.showerror("Šablon fakture", "Originalni Excel šablon nije pronađen.")
        return
    try:
        folder = invoice_dir() / "Predlosci"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        preview = folder / f"OpsNest_originalni_sablon_{stamp}.xlsx"
        shutil.copy2(TEMPLATE_XLSX, preview)
        try:
            preview.chmod(0o444)
        except OSError:
            pass
        open_path(preview)
    except Exception as exc:
        messagebox.showerror("Šablon fakture", f"Šablon nije moguće otvoriti:\n{exc}")


class MainApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        if APP_ICON_FILE.exists():
            try:
                self.iconbitmap(default=str(APP_ICON_FILE))
            except tk.TclError:
                pass
        screen_width, screen_height = desktop_work_area(self)
        minimum_width = min(1120, screen_width)
        minimum_height = min(720, screen_height)
        window_width = min(1620, max(minimum_width, screen_width - 80))
        window_height = min(980, max(minimum_height, screen_height - 100))
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(minimum_width, minimum_height)
        configure_style(self)
        self._windowed_geometry = f"{window_width}x{window_height}"
        self._window_mode_button: ttk.Button | None = None

        self.db = Database()
        self.company = self.db.get_company()
        self.ui_language = set_active_ui_language(self.company.get("ui_language"))
        self.authenticated = False
        self._workspace_built = False
        self._access_gate: ttk.Frame | None = None
        self.shell: ttk.Frame | None = None
        self.subscription_status_label: ttk.Label | None = None
        self._window_mode_button: ttk.Button | None = None
        self._startup_refresh_job: str | None = None
        self._pdf_export_active = False
        self._auto_pdf_queue: list[int] = []
        self._auto_pdf_tasks: dict[int, Callable[[], dict[str, Path]]] = {}
        self._auto_pdf_rerun_tasks: dict[int, Callable[[], dict[str, Path]]] = {}
        self._auto_pdf_active_invoice_id: int | None = None
        self._project_finance_dialogs: dict[int, tk.Toplevel] = {}
        self._update_metadata: dict[str, str] = {}
        self._automatic_reminder_check_started = False
        self._recurring_generation_started = False
        self.access_logo: ImageTk.PhotoImage | None = None
        self.language_var = tk.StringVar(value=self.ui_language.upper())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_access_gate()
        self.after_idle(self._maximize_window)
        self.after_idle(self._restore_saved_team_session)

    def _restore_saved_team_session(self) -> None:
        """Open a previously authenticated workspace on this trusted device.

        The server-issued member session is already DPAPI-protected in the
        local database and is independently revocable by the owner.  The
        password is never stored.  If the session was revoked or belongs to a
        different Windows account, ``cloud_connection`` safely clears it and
        leaves the normal sign-in card visible.
        """
        if self._workspace_built or not self.db.has_persisted_team_session():
            return
        self.activate_workspace()

    def _build_access_gate(self) -> None:
        if self._access_gate is not None and self._access_gate.winfo_exists():
            self._access_gate.destroy()
        gate = ttk.Frame(self, style="App.TFrame")
        gate.pack(fill="both", expand=True, padx=18, pady=18)
        gate.columnconfigure(0, weight=1)
        gate.rowconfigure(0, weight=1)
        self._access_gate = gate

        card = ttk.Frame(gate, style="Panel.TFrame", relief="solid", borderwidth=1, padding=34)
        card.grid(row=0, column=0)
        brand = ttk.Frame(card, style="Panel.TFrame")
        brand.pack(fill="x", anchor="w", pady=(0, 20))
        self.access_logo = load_logo_photo(APP_LOGO_FILE, 112)
        if self.access_logo is not None:
            ttk.Label(brand, image=self.access_logo, style="Panel.TLabel").pack(side="left", padx=(0, 16))
        brand_copy = ttk.Frame(brand, style="Panel.TFrame")
        brand_copy.pack(side="left", fill="y")
        ttk.Label(brand_copy, text=APP_NAME, style="AccessBrand.TLabel").pack(anchor="w")
        ttk.Label(brand_copy, text=tr("Fakture, kupci, projekti i naplate"), style="AccessSub.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(card, text=tr("Pristup firmi"), style="Section.TLabel").pack(anchor="w", pady=(0, 2))
        has_login = self.db.company_has_local_login()
        access_text = (
            tr("Prijavite se da otvorite fakture, kupce, projekte i naplate.")
            if has_login
            else tr("Prvo registrujte firmu i postavite e-mail i PIN za lokalnu prijavu.")
        )
        ttk.Label(card, text=access_text, style="AccessHelp.TLabel", wraplength=500).pack(anchor="w", pady=(0, 20))
        ttk.Label(card, text=self.subscription_status_text(), style="AccessHelp.TLabel", wraplength=500).pack(anchor="w", pady=(0, 16))

        # The access card is deliberately narrow enough to work on smaller
        # displays.  Do not pack every action into one horizontal row: that
        # hid the most important "Prijava u tim" action outside the card.
        actions = ttk.Frame(card, style="Panel.TFrame")
        actions.pack(fill="x")
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(
            actions,
            text="Prijava u tim (e-mail i lozinka)",
            style="Primary.TButton",
            command=self.open_team_login,
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Button(
            actions,
            text=tr("Prijavi se"),
            command=self.open_local_login,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            actions,
            text=tr("Registruj firmu"),
            command=lambda: self.open_company_registration(from_access=True),
        ).grid(row=1, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(
            actions,
            text=tr("Paketi i plaćanje"),
            command=self.open_plan_and_billing,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        website = ttk.Label(card, text="opsnestone.com", style="Link.TLabel", cursor="hand2")
        website.pack(anchor="w", pady=(14, 0))
        website.bind("<Button-1>", lambda _event: self.open_opsnest_website())
        add_tooltip(website, "Otvori OpsNest sajt")

        ttk.Label(
            card,
            text="Lokalni PIN štiti ovaj računar. Članovi tima se prijavljuju svojim e-mailom i lozinkom, pa preuzimaju zajedničke podatke.",
            style="AccessHelp.TLabel",
            wraplength=500,
        ).pack(anchor="w", pady=(20, 10))
        language_row = ttk.Frame(card, style="Panel.TFrame")
        language_row.pack(anchor="w")
        ttk.Label(language_row, text=tr("Jezik programa"), style="CardTitle.TLabel").pack(side="left", padx=(0, 8))
        self.language_combo = ttk.Combobox(
            language_row,
            textvariable=self.language_var,
            values=("SR", "EN", "DE", "BG", "RU"),
            width=5,
            state="readonly",
            style="Modern.TCombobox",
        )
        self.language_combo.pack(side="left")
        self.language_combo.bind("<<ComboboxSelected>>", self._on_header_language_selected)
        add_tooltip(self.language_combo, "Jezik interfejsa: SR = Srpski, EN = English, DE = Deutsch, BG = Български, RU = Русский.")

    def _build_workspace(self) -> None:
        self.shell = ttk.Frame(self, style="App.TFrame")
        self.shell.pack(fill="both", expand=True)

        self.header = ttk.Frame(self.shell, style="Hero.TFrame")
        self.header.pack(side="top", fill="x", padx=14, pady=(10, 8))
        self.header.columnconfigure(1, weight=1)
        self.brand_logo = load_logo_photo(APP_LOGO_FILE)
        if self.brand_logo is not None:
            ttk.Label(self.header, image=self.brand_logo, style="Hero.TLabel").grid(row=0, column=0, padx=(14, 10), pady=10, sticky="w")
        brand = ttk.Frame(self.header, style="Hero.TFrame")
        brand.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=10)
        ttk.Label(brand, text=APP_NAME, style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(brand, text="Fakture, kupci, projekti i naplate", style="HeroSub.TLabel").pack(anchor="w", pady=(1, 0))
        website = ttk.Label(brand, text="opsnestone.com", style="HeroLink.TLabel", cursor="hand2")
        website.pack(anchor="w", pady=(2, 0))
        website.bind("<Button-1>", lambda _event: self.open_opsnest_website())
        add_tooltip(website, "Otvori OpsNest sajt")
        self.subscription_status_label = ttk.Label(brand, text="", style="HeroSub.TLabel", wraplength=760)
        self.subscription_status_label.pack(anchor="w", pady=(4, 0))

        self.header_actions = ttk.Frame(self.header, style="Hero.TFrame")
        self.header_actions.grid(row=0, column=2, sticky="e", padx=(0, 14), pady=10)
        action_bar = ttk.Frame(self.header_actions, style="Hero.TFrame")
        action_bar.pack(anchor="e")
        ttk.Button(action_bar, text="Šabloni fakture", style="Header.TButton", command=self.open_invoice_templates).pack(side="left", padx=(0, 6))
        ttk.Button(action_bar, text="Novi projekat", style="HeaderPrimary.TButton", command=self.open_new_project).pack(side="left", padx=(0, 6))
        ttk.Button(action_bar, text="Osveži", style="Header.TButton", command=self.refresh_all).pack(side="left", padx=(0, 6))
        self.approval_header_button = ttk.Button(action_bar, text="Odobrenja", style="Header.TButton", command=self.open_invoice_approvals)
        self.approval_header_button.pack(side="left", padx=(0, 6))
        ttk.Button(action_bar, text="Paketi i plaćanje", style="Header.TButton", command=self.open_plan_and_billing).pack(side="left", padx=(0, 6))
        ttk.Button(action_bar, text="Smanji", style="Header.TButton", command=self.iconify).pack(side="left", padx=(0, 6))
        self._window_mode_button = ttk.Button(action_bar, text="Uvećaj", style="Header.TButton", command=self.toggle_window_mode)
        self._window_mode_button.pack(side="left", padx=(0, 6))
        self.language_combo = ttk.Combobox(
            action_bar,
            textvariable=self.language_var,
            values=("SR", "EN", "DE", "BG", "RU"),
            width=4,
            state="readonly",
            style="Modern.TCombobox",
        )
        self.language_combo.pack(side="left")
        self.language_combo.bind("<<ComboboxSelected>>", self._on_header_language_selected)
        add_tooltip(self.language_combo, "Jezik interfejsa: SR = Srpski, EN = English, DE = Deutsch, BG = Български, RU = Русский.")

        self.body = ttk.Frame(self.shell, style="App.TFrame")
        self.body.pack(side="top", fill="both", expand=True, padx=14, pady=(0, 14))

        self.tabs = ttk.Notebook(self.body)
        self.tabs.pack(fill="both", expand=True)

        self.dashboard_tab = DashboardTab(self.tabs, self)
        self.banking_tab = BankingTab(self.tabs, self)
        self.financial_control_tab = FinancialControlTab(self.tabs, self)
        self.customers_tab = CustomersTab(self.tabs, self)
        self.projects_tab = ProjectsTab(self.tabs, self)
        self.backup_tab = BackupTab(self.tabs, self)

        self.tabs.add(self.projects_tab, text="Firma i projekti")
        self.tabs.add(self.dashboard_tab, text="Dashboard")
        self.tabs.add(self.banking_tab, text="Banka")
        self.tabs.add(self.financial_control_tab, text="Finansije")
        self.tabs.add(self.customers_tab, text="Kupci")
        self.tabs.add(self.backup_tab, text="Backup")
        self.tabs.select(self.projects_tab)
        self.refresh_subscription_status_indicator()
        self.refresh_invoice_approval_badge()

    def subscription_status_text(self) -> str:
        subscription = self.db.get_subscription()
        status = str(subscription.get("status") or "not_started").lower()
        if status == "trial":
            days = int(subscription.get("days_remaining") or 0)
            if days <= 1:
                return subscription_copy("trial_last_day")
            return subscription_copy("trial", days=days)
        if status == "active":
            return subscription_copy("active", plan=str(subscription.get("plan_code") or "starter").title())
        if status == "legacy":
            return subscription_copy("legacy")
        if status == "not_started":
            return subscription_copy("not_started")
        return subscription_copy("expired")

    def refresh_subscription_status_indicator(self) -> None:
        if self.subscription_status_label is not None and self.subscription_status_label.winfo_exists():
            self.subscription_status_label.configure(text=self.subscription_status_text())

    def open_online_activation(self, *, prefill_company: str = "", prefill_email: str = "") -> None:
        """Verify a business e-mail in the desktop app, without opening a browser."""
        subscription = self.db.get_subscription()
        connection = self.db.cloud_connection()
        if connection["api_url"] and connection["workspace_token"]:
            self.open_plan_and_billing()
            return
        try:
            client = OpsNestCloudClient(OPSNEST_CLOUD_API_URL)
        except CloudApiError as exc:
            messagebox.showerror("Online aktivacija", str(exc), parent=self)
            return
        OnlineActivationDialog(
            self,
            self,
            client=client,
            api_url=OPSNEST_CLOUD_API_URL,
            workspace_id=str(subscription["workspace_id"]),
            prefill_company=prefill_company,
            prefill_email=prefill_email,
        )

    def refresh_online_license(self, connection: dict[str, str] | None = None, *, silent: bool = False) -> dict[str, Any] | None:
        """Refresh cloud billing data while keeping the desktop usable offline."""
        connection = connection or self.db.cloud_connection()
        subscription = self.db.get_subscription()
        try:
            client = OpsNestCloudClient(connection.get("api_url") or OPSNEST_CLOUD_API_URL)
            workspace_id = str(subscription["workspace_id"])
            if connection.get("workspace_token"):
                license_data = client.license_status(
                    workspace_id=workspace_id,
                    workspace_token=connection["workspace_token"],
                )
            elif connection.get("member_id") and connection.get("member_token"):
                # Team devices intentionally do not retain the legacy billing
                # token. Their revocable central session supplies the same safe
                # entitlement summary without exposing billing credentials.
                license_data = client.team_license_status(
                    workspace_id=workspace_id,
                    member_id=connection["member_id"],
                    member_token=connection["member_token"],
                )
            else:
                raise CloudApiError("Prvo se prijavite centralnim nalogom firme da bi licenca mogla da se osveži.")
            self.db.apply_subscription_update(
                status=str(license_data.get("status") or "verification_pending"),
                plan_code=str(license_data.get("plan_code") or "starter"),
                billing_provider="opsnest_cloud",
                verified_at=str(license_data.get("last_verified_at") or datetime.now().isoformat(timespec="seconds")),
                trial_started_at=str(license_data.get("trial_started_at") or ""),
                trial_ends_at=str(license_data.get("trial_ends_at") or ""),
            )
            self.refresh_subscription_status_indicator()
        except CloudApiError as exc:
            self.db.record_cloud_sync_error(str(exc))
            if not silent:
                messagebox.showerror("Online licenca", str(exc), parent=self)
            return None
        if not silent:
            messagebox.showinfo("Online licenca", "Status licence je osvežen.", parent=self)
        return license_data

    def open_plan_and_billing(self) -> None:
        """Open the permanent package, trial, renewal and support center."""
        PlanAndBillingDialog(self, self)

    def plan_includes_feature(self, feature: str) -> bool:
        details = self.db.plan_usage().get("details") or {}
        return feature in set(details.get("features") or ())

    def is_owner_or_administrator(self) -> bool:
        return self.active_team_role() in {"", "owner", "administrator"}

    def active_team_member_name(self) -> str:
        connection = self.db.cloud_connection()
        return str(
            connection.get("member_name")
            or self.company.get("director_name")
            or self.company.get("name")
            or "Vlasnik"
        ).strip()

    def invoice_actor_payload(self) -> dict[str, str]:
        return {
            "prepared_by_role": self.active_team_role() or "owner",
            "prepared_by_name": self.active_team_member_name(),
        }

    def invoice_approval_enabled(self) -> bool:
        return bool(int(self.company.get("team_invoice_approval_required") or 0)) and self.plan_includes_feature(
            "invoice_approval"
        )

    def refresh_invoice_approval_badge(self) -> None:
        button = getattr(self, "approval_header_button", None)
        if button is None or not button.winfo_exists():
            return
        if self.invoice_approval_enabled() and self.is_owner_or_administrator():
            pending_count = self.db.pending_invoice_approval_count()
            caption = tr("Odobrenja ({count})").format(count=pending_count) if pending_count else tr("Odobrenja")
            button.configure(text=caption, state="normal")
        else:
            button.configure(text=tr("Odobrenja"), state="disabled")

    def open_invoice_templates(self) -> None:
        InvoiceTemplateDialog(self, self)

    def open_invoice_approvals(self) -> None:
        if not self.invoice_approval_enabled():
            messagebox.showinfo(
                "Odobravanje faktura",
                "Odobravanje faktura je dostupno u Business i Pro paketu.",
                parent=self,
            )
            return
        if not self.require_team_permission({"owner", "administrator"}, "pregled i odobravanje faktura", parent=self):
            return
        InvoiceApprovalDialog(self, self)

    def open_team_members(self) -> None:
        """Manage centrally authenticated users and the shared workspace copy."""
        TeamMembersDialog(self, self)

    def active_team_role(self) -> str:
        """Return the locally stored central role; local-only workspaces retain full control."""
        connection = self.db.cloud_connection()
        return str(connection.get("member_role") or "").strip().lower()

    def require_team_permission(
        self,
        allowed_roles: set[str],
        action: str,
        *,
        parent: tk.Widget | None = None,
    ) -> bool:
        """Keep normal local installations unrestricted while enforcing central team roles."""
        role = self.active_team_role()
        if not role or role in allowed_roles:
            return True
        labels = {
            "owner": "Vlasnik / administrator",
            "administrator": "Administrator",
            "project_manager": "Menadžer projekta",
            "accountant": "Knjigovođa",
            "operator": "Operater",
        }
        messagebox.showwarning(
            "OpsNest tim",
            f"Za radnju '{action}' potrebna je druga uloga. Trenutno ste prijavljeni kao: {labels.get(role, role)}.",
            parent=parent or self,
        )
        return False

    def _team_client(self) -> OpsNestCloudClient:
        connection = self.db.cloud_connection()
        return OpsNestCloudClient(connection.get("api_url") or OPSNEST_CLOUD_API_URL)

    def team_connection_ready(self) -> tuple[dict[str, str], str] | None:
        """Return the local device's revocable team session, if it has one."""
        connection = self.db.cloud_connection()
        subscription = self.db.get_subscription()
        workspace_id = str(subscription.get("workspace_id") or "").strip()
        if not workspace_id or not connection.get("member_id") or not connection.get("member_token"):
            return None
        return connection, workspace_id

    def download_team_data(
        self,
        *,
        parent: tk.Widget,
        confirm: bool = True,
        allow_empty_owner_workspace: bool = False,
    ) -> bool:
        """Safely replace this device's data with the latest shared revision."""
        ready = self.team_connection_ready()
        if not ready:
            messagebox.showinfo(
                "OpsNest tim",
                "Prvo se prijavite centralnim nalogom ili prihvatite poziv vlasnika firme.",
                parent=parent,
            )
            return False
        connection, workspace_id = ready
        try:
            payload = self._team_client().download_team_snapshot(
                workspace_id=workspace_id,
                member_id=connection["member_id"],
                member_token=connection["member_token"],
            )
        except CloudApiError as exc:
            self.db.record_cloud_sync_error(str(exc))
            messagebox.showerror("OpsNest tim", str(exc), parent=parent)
            return False
        revision = int(payload.get("revision") or 0)
        if revision <= 0 or not payload.get("snapshot_b64"):
            self.db.mark_cloud_sync(0, "")
            if allow_empty_owner_workspace and self.active_team_role() in {"owner", "administrator"}:
                messagebox.showinfo(
                    "OpsNest tim",
                    "Prijavljeni ste kao vlasnik / administrator. Zajednički prostor još nema "
                    "poslate podatke, ali možete odmah otvoriti Desktop i postaviti početni profil firme.",
                    parent=parent,
                )
                return True
            messagebox.showinfo(
                "OpsNest tim",
                "Zajednički prostor je prazan. Vlasnik ili administrator prvo treba da pošalje podatke sa početnog računara.",
                parent=parent,
            )
            return False
        local_state = self.db.cloud_sync_state()
        local_revision = local_state["revision"]
        change_status = self.db.cloud_sync_change_status()
        if revision == local_revision:
            if change_status.get("has_unsynced_changes"):
                messagebox.showwarning(
                    "OpsNest tim",
                    "Ovaj računar ima lokalne izmene koje još nisu poslate. "
                    "Pošaljite ih u zajednički prostor pre rada na drugom računaru.",
                    parent=parent,
                )
                return False
            messagebox.showinfo("OpsNest tim", "Ovaj računar već ima najnoviju zajedničku verziju podataka.", parent=parent)
            return True
        if change_status.get("has_unsynced_changes"):
            messagebox.showwarning(
                "OpsNest tim",
                "Preuzimanje je zaustavljeno: ovaj računar ima lokalne poslovne izmene koje nisu u zajedničkoj verziji. "
                "Prvo ih pošaljite ili napravite provereni backup i odlučite koja verzija je merodavna.",
                parent=parent,
            )
            return False
        baseline_note = ""
        if change_status.get("baseline_unknown"):
            baseline_note = (
                "\n\nOvaj računar je sinhronizovan starijom verzijom OpsNesta bez kontrolnog zbira. "
                "OpsNest će prvo napraviti lokalni backup; proverite da li je zajednička verzija merodavna."
            )
        if confirm and not messagebox.askyesno(
            "Preuzmi zajedničke podatke",
            "Zajednička verzija će zameniti lokalne poslovne podatke na ovom računaru. "
            f"OpsNest prvo pravi lokalni backup. Nastaviti?{baseline_note}",
            parent=parent,
        ):
            return False
        try:
            self.db.apply_cloud_sync_snapshot(str(payload["snapshot_b64"]), str(payload["sha256"]))
            self.db.mark_cloud_sync(revision, str(payload.get("sha256") or ""))
            self.company = self.db.get_company()
            self.refresh_all()
        except (ValueError, OSError) as exc:
            self.db.record_cloud_sync_error(str(exc))
            messagebox.showerror("OpsNest tim", f"Podaci nisu preuzeti: {exc}", parent=parent)
            return False
        messagebox.showinfo(
            "OpsNest tim",
            f"Preuzeta je zajednička verzija #{revision}. Prethodno lokalno stanje je sačuvano u backup folderu.",
            parent=parent,
        )
        return True

    def build_portal_financial_overview(self) -> dict[str, Any]:
        """Build the only numeric finance payload shared with Workspace.

        It intentionally contains one company currency and aggregate totals
        only. No invoice, supplier, customer, project, document, bank-row,
        account number or attachment leaves the Desktop application here.
        """
        currency = str(self.company.get("default_currency") or DEFAULT_CURRENCY).upper()
        finance = self.db.company_financial_summary()
        pnl = dict(finance.get("currencies", {}).get(currency) or {})
        forecast = self.db.cash_flow_forecast(days=90)
        flow = dict(forecast.get("currencies", {}).get(currency) or {})
        invoices = [row for row in self.db.list_invoices(open_only=True) if str(row.get("currency") or "").upper() == currency]
        receivables = sum((money_round(row.get("balance_total")) for row in invoices), Decimal("0"))
        overdue = sum(
            (money_round(row.get("balance_total")) for row in invoices if (parse_date(row.get("due_date")) or date.today()) < date.today()),
            Decimal("0"),
        )
        payables = sum(
            (money_round(row.get("balance_amount")) for row in self.db.list_vendor_bills(include_paid=False) if str(row.get("currency") or "").upper() == currency),
            Decimal("0"),
        )
        return {
            "currency": currency,
            "horizon_days": 90,
            "income_net": float(money_round(pnl.get("income_net"))),
            "expense_net": float(money_round(pnl.get("expense_net"))),
            "profit_net": float(money_round(pnl.get("profit_net"))),
            "vat_payable": float(money_round(pnl.get("vat_payable"))),
            "open_receivables": float(money_round(receivables)),
            "overdue_receivables": float(money_round(overdue)),
            "open_payables": float(money_round(payables)),
            "opening_cash": float(money_round(flow.get("opening_balance"))),
            "forecast_inflows": float(money_round(flow.get("inflows"))),
            "forecast_outflows": float(money_round(flow.get("outflows"))),
            "forecast_closing": float(money_round(flow.get("closing_balance"))),
        }

    def upload_team_data(self, *, parent: tk.Widget) -> bool:
        """Send a checksum-protected revision after an explicit user action."""
        ready = self.team_connection_ready()
        if not ready:
            messagebox.showinfo("OpsNest tim", "Prvo se prijavite centralnim nalogom.", parent=parent)
            return False
        connection, workspace_id = ready
        try:
            snapshot = self.db.build_cloud_sync_snapshot()
            expected_revision = int(self.db.cloud_sync_state()["revision"])
            result = self._team_client().upload_team_snapshot(
                workspace_id=workspace_id,
                member_id=connection["member_id"],
                member_token=connection["member_token"],
                expected_revision=expected_revision,
                snapshot_b64=snapshot["snapshot_b64"],
                sha256=snapshot["sha256"],
                financial_audit_hash=snapshot["financial_audit_hash"],
                financial_audit_count=int(snapshot["financial_audit_count"]),
            )
            revision = int(result.get("revision") or 0)
            self.db.mark_cloud_sync(revision, str(result.get("sha256") or snapshot["sha256"]))
            overview_message = ""
            try:
                self._team_client().upload_financial_overview(
                    workspace_id=workspace_id,
                    member_id=connection["member_id"],
                    member_token=connection["member_token"],
                    summary=self.build_portal_financial_overview(),
                )
            except (CloudApiError, ValueError, OSError) as overview_error:
                # The shared Desktop revision is already durable. Do not make
                # it look failed merely because the optional web dashboard
                # summary was temporarily unavailable.
                overview_message = f"\nZbirni pregled za web nije osvežen: {overview_error}"
        except CloudApiError as exc:
            self.db.record_cloud_sync_error(str(exc))
            if "noviju verziju" in str(exc).lower() or "revision" in str(exc).lower():
                messagebox.showwarning(
                    "OpsNest tim",
                    "Drugi član je već poslao noviju verziju. Prvo preuzmite zajedničke podatke, pa ponovite svoju izmenu.",
                    parent=parent,
                )
            else:
                messagebox.showerror("OpsNest tim", str(exc), parent=parent)
            return False
        except (ValueError, OSError) as exc:
            self.db.record_cloud_sync_error(str(exc))
            messagebox.showerror("OpsNest tim", f"Podaci nisu poslati: {exc}", parent=parent)
            return False
        messagebox.showinfo("OpsNest tim", f"Zajednička verzija #{revision} je bezbedno poslata.{overview_message}", parent=parent)
        return True

    def require_plan_feature(self, feature: str, *, parent: tk.Widget | None = None) -> bool:
        try:
            self.db.assert_plan_feature(feature)
        except PlanLimitError as exc:
            if messagebox.askyesno(
                "OpsNest paket",
                f"{exc}\n\nDa li želite da otvorite pakete i plaćanje?",
                parent=parent or self,
            ):
                self.open_plan_and_billing()
            return False
        return True

    def open_plan_checkout(
        self,
        client: OpsNestCloudClient | None = None,
        subscription: dict[str, Any] | None = None,
        plan_code: str = "",
    ) -> None:
        subscription = subscription or self.db.get_subscription()
        client = client or OpsNestCloudClient(OPSNEST_CLOUD_API_URL)
        connection = self.db.cloud_connection()
        if not connection["workspace_token"]:
            messagebox.showinfo(
                "Paketi i plaćanje",
                "Prvo potvrdite poslovni e-mail. Probni period već počinje pri registraciji firme, a potvrda samo povezuje plaćanje i podršku.",
                parent=self,
            )
            self.open_online_activation()
            return
        try:
            readiness = client.billing_readiness(
                workspace_id=str(subscription["workspace_id"]),
                workspace_token=connection["workspace_token"],
            )
        except CloudApiError as exc:
            self.db.record_cloud_sync_error(str(exc))
            messagebox.showerror("PayPal", str(exc), parent=self)
            return
        if not bool(readiness.get("ready")):
            mode = str(readiness.get("mode") or "sandbox").lower()
            messagebox.showinfo(
                "PayPal",
                (
                    "PayPal paketi još nisu povezani. Potrebni su PayPal Client ID, Client Secret, Webhook ID "
                    "i po jedan Subscription Plan ID za Starter, Business, Pro i AI savetnika. "
                    f"Trenutni režim: {mode}."
                ),
                parent=self,
            )
            return
        normalized_plan = str(plan_code or "").strip().lower()
        if not normalized_plan:
            chooser = SubscriptionPlanDialog(self)
            self.wait_window(chooser)
            normalized_plan = str(chooser.selected_plan or "").strip().lower()
        if not normalized_plan:
            return
        ai_ready = readiness.get("ai_advisor_ready") if isinstance(readiness.get("ai_advisor_ready"), dict) else {}
        if normalized_plan.startswith("ai_") and not bool(ai_ready.get(normalized_plan)):
            messagebox.showinfo(
                "AI savetnik",
                "Izabrani AI dodatak još nije povezan sa PayPal Subscription Plan-om. Pokušajte ponovo kada bude aktiviran.",
                parent=self,
            )
            return
        try:
            checkout = client.checkout_url(
                plan_code=normalized_plan,
                workspace_id=str(subscription["workspace_id"]),
                workspace_token=connection["workspace_token"],
            )
            webbrowser.open(checkout)
            messagebox.showinfo(
                "PayPal",
                "PayPal checkout je otvoren u pregledaču. Po završetku se vratite u OpsNest i kliknite Osveži status u prozoru Paketi i plaćanje.",
                parent=self,
            )
        except CloudApiError as exc:
            self.db.record_cloud_sync_error(str(exc))
            messagebox.showerror("PayPal", str(exc), parent=self)

    def send_safe_diagnostics(self, message: str = "") -> bool:
        """Send only product support metadata, never local accounting content."""
        connection = self.db.cloud_connection()
        subscription = self.db.get_subscription()
        if not connection["workspace_token"]:
            messagebox.showinfo("OpsNest podrška", "Prvo potvrdite poslovni e-mail da biste poslali dijagnostiku podršci.", parent=self)
            return False
        try:
            client = OpsNestCloudClient(OPSNEST_CLOUD_API_URL)
            client.send_diagnostics(
                workspace_id=str(subscription["workspace_id"]),
                workspace_token=connection["workspace_token"],
                app_version=OPSNEST_APP_VERSION,
                operating_system=f"{platform.system()} {platform.release()}",
                license_status=str(subscription.get("status") or ""),
                message=message,
            )
        except CloudApiError as exc:
            self.db.record_cloud_sync_error(str(exc))
            messagebox.showerror("OpsNest podrška", str(exc), parent=self)
            return False
        messagebox.showinfo("OpsNest podrška", "Dijagnostika je poslata podršci. Fakture, PDF-ovi, prilozi, lozinke i PIN nisu poslati.", parent=self)
        return True

    def open_opsnest_website(self) -> None:
        """Open the public product site without exposing cloud-service configuration."""
        try:
            webbrowser.open_new_tab(OPSNEST_WEBSITE_URL)
        except OSError:
            messagebox.showerror("OpsNest", "Sajt trenutno nije moguće otvoriti.", parent=self)

    def refresh_all(self) -> None:
        if self._startup_refresh_job is not None:
            try:
                self.after_cancel(self._startup_refresh_job)
            except tk.TclError:
                pass
            self._startup_refresh_job = None
        self.company = self.db.get_company()
        self.refresh_subscription_status_indicator()
        self.refresh_invoice_approval_badge()
        if not self._workspace_built:
            return
        self.dashboard_tab.refresh()
        self.banking_tab.refresh()
        self.financial_control_tab.refresh()
        self.customers_tab.refresh()
        self.projects_tab.refresh()
        self.backup_tab.refresh()

    def _refresh_secondary_tabs_after_startup(self) -> None:
        """Fill secondary views after the project-first screen is already visible."""
        self._startup_refresh_job = None
        if not self._workspace_built:
            return
        self.dashboard_tab.refresh()
        self.banking_tab.refresh()
        self.financial_control_tab.refresh()
        self.customers_tab.refresh()
        self.projects_tab.refresh()
        self.backup_tab.refresh()

    def _refresh_open_dialogs_for_language(self, code: str) -> None:
        """Apply the selected language to dialogs that are already open."""
        dialogs: list[tk.Toplevel] = []
        pending = list(self.winfo_children())
        seen: set[str] = set()
        while pending:
            widget = pending.pop()
            widget_id = str(widget)
            if widget_id in seen:
                continue
            seen.add(widget_id)
            try:
                pending.extend(widget.winfo_children())
            except tk.TclError:
                continue
            if isinstance(widget, tk.Toplevel):
                dialogs.append(widget)

        for dialog in dialogs:
            try:
                if not dialog.winfo_exists():
                    continue
                localize_widget_tree(dialog, code)
                refresh = getattr(dialog, "refresh", None)
                if callable(refresh):
                    refresh()
            except tk.TclError:
                continue

    def apply_language(self, value: Any, *, persist: bool = True) -> None:
        code = set_active_ui_language(value)
        self.ui_language = code
        if hasattr(self, "language_var"):
            self.language_var.set(code.upper())
        if persist:
            payload = self.db.get_company()
            payload["ui_language"] = code
            self.db.save_company(payload)
            self.company = self.db.get_company()
        if self._workspace_built:
            # Rebuild dynamic captions and table values as well as visible labels.
            self.refresh_all()
            localize_widget_tree(self, code)
            self._refresh_open_dialogs_for_language(code)
            self.refresh_subscription_status_indicator()
            self._update_window_button()
        else:
            self._build_access_gate()

    def _on_header_language_selected(self, event: tk.Event | None = None) -> None:
        self.apply_language(self.language_var.get().lower())

    def open_company_registration(self, *, from_access: bool = False) -> None:
        CompanyRegistrationDialog(self, self, from_access=from_access)

    def open_company_profile(self) -> None:
        dialog = CompanyProfileDialog(self, self)
        self.wait_window(dialog)
        if self._workspace_built:
            self.refresh_all()

    def open_local_login(self) -> None:
        if not self.db.company_has_local_login():
            messagebox.showinfo("Prijava", "Prvo registrujte firmu i postavite e-mail i PIN za prijavu.")
            return
        LocalLoginDialog(self, self)

    def open_team_login(self) -> None:
        """Open the central team sign-in / invitation acceptance dialog."""
        TeamSignInDialog(self, self)

    def activate_workspace(self) -> None:
        if self._workspace_built:
            return
        if self._access_gate is not None and self._access_gate.winfo_exists():
            self._access_gate.destroy()
        self._workspace_built = True
        self.authenticated = True
        self._build_workspace()
        self.apply_language(self.ui_language, persist=False)
        self.tabs.select(self.projects_tab)
        # Show the primary company/project screen immediately. The other tables
        # are loaded just after the window paints instead of blocking login.
        self.projects_tab.refresh()
        self.update_idletasks()
        self._startup_refresh_job = self.after(80, self._refresh_secondary_tabs_after_startup)
        # A centralized owner or team session also refreshes the entitlement.
        # This lets a linked device receive Founder/Pro access without storing
        # the legacy workspace billing token locally.
        self.after(900, lambda: self.refresh_online_license(silent=True))
        # A non-blocking check keeps releases discoverable without slowing login.
        self.after(1800, self.check_for_updates_silently)
        # Safe automations never issue documents: recurring invoices stay drafts,
        # while payment reminders require explicit SMTP opt-in in company settings.
        self.after(2200, self.generate_due_recurring_drafts_silently)
        self.after(2600, self.send_due_payment_reminders_silently)

    def generate_due_recurring_drafts_silently(self) -> None:
        """Create due recurring drafts after login without interrupting the owner."""
        if self._recurring_generation_started or not self._workspace_built:
            return
        self._recurring_generation_started = True
        try:
            created = self.db.generate_due_recurring_invoices()
        except (ValueError, OSError):
            return
        if created:
            self.refresh_all()

    def send_due_payment_reminders_silently(self) -> None:
        """Send opted-in, rate-limited text reminders without blocking the desktop UI."""
        if self._automatic_reminder_check_started or not self._workspace_built:
            return
        company = self.db.get_company()
        if not bool(int(company.get("auto_payment_reminders") or 0)):
            return
        if not (company.get("smtp_host") and (company.get("smtp_from_email") or company.get("email") or company.get("smtp_username"))):
            return
        self._automatic_reminder_check_started = True
        interval = int(company.get("payment_reminder_interval_days") or 7)
        try:
            candidates = self.db.automatic_payment_reminder_candidates(interval_days=interval)
        except (ValueError, OSError):
            return
        if not candidates:
            return

        sender_name = str(company.get("smtp_from_name") or company.get("name") or APP_NAME).strip()
        sender_email = str(company.get("smtp_from_email") or company.get("email") or company.get("smtp_username") or "").strip()
        language = self.ui_language
        jobs: list[tuple[int, str, str, EmailMessage]] = []
        for invoice in candidates:
            recipient = str(invoice.get("customer_email") or "").strip()
            subject = payment_reminder_copy("subject", language, number=invoice.get("invoice_number") or "-")
            message = EmailMessage()
            message["To"] = recipient
            message["From"] = formataddr((sender_name, sender_email))
            message["Subject"] = subject
            if company.get("smtp_reply_to"):
                message["Reply-To"] = str(company["smtp_reply_to"])
            message.set_content(payment_reminder_copy(
                "body",
                language,
                customer=invoice.get("customer_name") or "",
                number=invoice.get("invoice_number") or "-",
                amount=fmt_money(invoice.get("balance_total") or 0, invoice.get("currency") or DEFAULT_CURRENCY),
                due=display_date(invoice.get("due_date")),
                company=company.get("name") or APP_NAME,
            ))
            jobs.append((int(invoice["id"]), recipient, subject, message))

        results: queue.Queue[tuple[int, str, str, str]] = queue.Queue()
        finished = threading.Event()
        sent_count = [0]

        def worker() -> None:
            try:
                for invoice_id, recipient, subject, message in jobs:
                    try:
                        send_message_via_smtp(company, message)
                        results.put((invoice_id, recipient, subject, ""))
                    except Exception as exc:  # SMTP errors must never stop the desktop session.
                        results.put((invoice_id, recipient, subject, str(exc)))
            finally:
                finished.set()

        threading.Thread(target=worker, daemon=True).start()

        def collect_results() -> None:
            while True:
                try:
                    invoice_id, recipient, subject, error = results.get_nowait()
                except queue.Empty:
                    break
                if not error:
                    try:
                        self.db.record_payment_reminder(invoice_id, recipient, subject)
                        sent_count[0] += 1
                    except (ValueError, OSError):
                        pass
            if not finished.is_set():
                self.after(200, collect_results)
                return
            if sent_count[0]:
                self.refresh_all()

        self.after(250, collect_results)

    def check_for_updates_silently(self) -> None:
        """Fetch public update metadata without showing errors or blocking the UI."""
        def worker() -> None:
            try:
                result = OpsNestCloudClient(OPSNEST_CLOUD_API_URL).desktop_update()
            except CloudApiError:
                return
            self._update_metadata = {
                "latest_version": str(result.get("latest_version") or ""),
                "installer_url": str(result.get("installer_url") or ""),
                "installer_sha256": str(result.get("installer_sha256") or ""),
            }
        threading.Thread(target=worker, daemon=True).start()

    def launch_auto_update(self, installer_path: Path) -> bool:
        """Hand off an already verified installer and then close this app safely."""
        install_dir = installed_app_dir()
        if install_dir is None:
            messagebox.showinfo(
                tr("OpsNest ažuriranje"),
                plan_dialog_copy("update_manual_development"),
                parent=self,
            )
            return False
        try:
            subprocess.Popen(
                [str(installer_path), "--auto-update", "--install-dir", str(install_dir), "--restart"],
                cwd=str(installer_path.parent),
            )
        except OSError as exc:
            messagebox.showerror(tr("OpsNest ažuriranje"), str(exc), parent=self)
            return False
        self.after(350, self.on_close)
        return True

    def on_close(self) -> None:
        try:
            self.db.close()
        finally:
            self.destroy()

    def _maximize_window(self) -> None:
        self.update_idletasks()
        self._windowed_geometry = self.geometry()
        try:
            self.state("zoomed")
        except tk.TclError:
            screen_width, screen_height = desktop_work_area(self)
            width = min(screen_width - 40, 1620)
            height = min(screen_height - 80, 980)
            center_window(self, width, height)
        self._update_window_button()

    def _update_window_button(self) -> None:
        if self._window_mode_button is None:
            return
        self._window_mode_button.configure(text=tr("Vrati" if self.state() == "zoomed" else "Uvećaj"))

    def toggle_window_mode(self) -> None:
        if self.state() == "zoomed":
            try:
                self.state("normal")
            except tk.TclError:
                pass
            if self._windowed_geometry:
                try:
                    self.geometry(self._windowed_geometry)
                except tk.TclError:
                    screen_width, screen_height = desktop_work_area(self)
                    center_window(self, min(screen_width - 40, 1620), min(screen_height - 80, 980))
            else:
                screen_width, screen_height = desktop_work_area(self)
                center_window(self, min(screen_width - 40, 1620), min(screen_height - 80, 980))
        else:
            self._windowed_geometry = self.geometry()
            self._maximize_window()
            return
        self._update_window_button()

    def open_new_project(self) -> None:
        """Make the project-first workflow the primary action after login."""
        if not self._workspace_built:
            return
        self.tabs.select(self.projects_tab)
        self.projects_tab.start_new_project()

    def open_project_finance(
        self,
        project_id: int,
        *,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        """Reuse the active project workspace instead of stacking duplicate windows."""
        existing = self._project_finance_dialogs.get(project_id)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        dialog = ProjectFinanceDialog(self.projects_tab, self, project_id, on_changed=on_changed)
        self._project_finance_dialogs[project_id] = dialog

        def forget_dialog(event: tk.Event) -> None:
            if event.widget is dialog:
                self._project_finance_dialogs.pop(project_id, None)

        dialog.bind("<Destroy>", forget_dialog, add="+")

    def open_invoice_editor(
        self,
        invoice_id: int | None = None,
        *,
        project_id: int | None = None,
        correction_invoice_id: int | None = None,
        initial_tab: str = "details",
        invoice_kind: str = "standard",
    ) -> None:
        if not self.authenticated:
            return
        if invoice_id is None and not project_id and not correction_invoice_id:
            # A project is the owner of every new invoice and its complete archive.
            self.tabs.select(self.projects_tab)
            messagebox.showinfo(
                "Faktura je vezana za projekat",
                "Novu fakturu otvorite iz projekta:\n\n"
                "Firma i projekti > izaberite projekat > Otvori projekat > Nova faktura za projekat.\n\n"
                "Tako se faktura, PDF, Excel kopija, uplate i prilozi automatski vode u istom projektu.",
            )
            return
        editor = InvoiceEditor(
            self,
            self.db,
            invoice_id=invoice_id,
            initial_project_id=project_id,
            correction_invoice_id=correction_invoice_id,
            initial_tab=initial_tab,
            initial_invoice_kind=invoice_kind,
        )
        localize_widget_tree(editor, self.ui_language)
        self.wait_window(editor)
        self.refresh_all()

    def archive_invoice_outputs(self, invoice_id: int) -> dict[str, Path]:
        """Keep the exact Excel workbook and its native PDF together in the project archive."""
        invoice = self.db.invoice_export_payload(invoice_id)
        if not invoice:
            raise ValueError("Faktura ne postoji.")
        archive_dir = self.db.invoice_archive_dir(invoice_id)
        company_logo = Path(self.company.get("logo_path") or LOGO_FILE)
        template_path = self.db.invoice_template_path(int(invoice.get("invoice_template_id") or 0))
        return export_invoice_bundle(invoice, archive_dir, template_path=template_path, logo_path=company_logo)

    def prepare_invoice_output_task(self, invoice_id: int) -> Callable[[], dict[str, Path]]:
        """Read SQLite data on the UI thread, then export the captured snapshot in a worker."""
        invoice = self.db.invoice_export_payload(invoice_id)
        if not invoice:
            raise ValueError("Faktura ne postoji.")
        archive_dir = self.db.invoice_archive_dir(invoice_id)
        company_logo = Path(self.company.get("logo_path") or LOGO_FILE)
        template_path = self.db.invoice_template_path(int(invoice.get("invoice_template_id") or 0))
        return lambda: export_invoice_bundle(invoice, archive_dir, template_path=template_path, logo_path=company_logo)

    def archive_credit_note_outputs(self, credit_note_id: int) -> dict[str, Path]:
        """Archive the formal credit note with the project that owns its source invoice."""
        note = self.db.credit_note_export_payload(credit_note_id)
        if not note:
            raise ValueError("Odobrenje ne postoji.")
        archive_dir = self.db.credit_note_archive_dir(credit_note_id)
        logo = Path(note.get("company", {}).get("logo_path") or LOGO_FILE)
        return export_credit_note_bundle(note, archive_dir, logo_path=logo)

    def prepare_credit_note_output_task(self, credit_note_id: int) -> Callable[[], dict[str, Path]]:
        """Capture the immutable note snapshot before rendering it off the UI thread."""
        note = self.db.credit_note_export_payload(credit_note_id)
        if not note:
            raise ValueError("Odobrenje ne postoji.")
        archive_dir = self.db.credit_note_archive_dir(credit_note_id)
        logo = Path(note.get("company", {}).get("logo_path") or LOGO_FILE)
        return lambda: export_credit_note_bundle(note, archive_dir, logo_path=logo)

    def prepare_project_vat_evidence_task(
        self,
        project_id: int,
        period_from: str,
        period_to: str,
        report_language: str | None = None,
    ) -> tuple[dict[str, Any], Callable[[], dict[str, Path]]]:
        """Capture a project VAT working ledger before exporting it outside the UI thread."""
        report = self.db.project_vat_evidence(project_id, period_from, period_to)
        report["report_language"] = normalize_ui_language(report_language or self.ui_language)
        archive_dir = self.db.project_vat_reports_dir(project_id, report.get("period_from"))
        return report, lambda: export_project_vat_evidence_bundle(report, archive_dir)

    def prepare_project_accountant_task(
        self,
        project_id: int,
        period_from: str,
        period_to: str,
        report_language: str | None = None,
    ) -> tuple[dict[str, Any], Callable[[], dict[str, Path]]]:
        """Capture the project period on the UI thread before creating the export in background."""
        report = self.db.project_accountant_report(project_id, period_from, period_to)
        report["report_language"] = normalize_ui_language(report_language or self.ui_language)
        archive_dir = self.db.project_accountant_reports_dir(project_id, report.get("period_from"))
        return report, lambda: export_project_accountant_bundle(report, archive_dir)

    def credit_note_output_path(self, credit_note_id: int, format_name: str) -> Path:
        note = self.db.credit_note_export_payload(credit_note_id)
        if not note:
            raise ValueError("Odobrenje ne postoji.")
        suffix = "pdf" if format_name == "pdf" else "xlsx"
        base = safe_filename(f"odobrenje_{note.get('credit_note_number') or credit_note_id}")
        return self.db.credit_note_archive_dir(credit_note_id) / f"{base}.{suffix}"

    def open_or_generate_credit_note_output(self, credit_note_id: int, format_name: str = "pdf") -> bool:
        try:
            output = self.credit_note_output_path(credit_note_id, format_name)
        except Exception as exc:
            messagebox.showerror("Odobrenje", f"Dokument nije dostupan:\n{exc}", parent=self)
            return False
        if output.is_file() and output.stat().st_size > 0:
            open_path(output)
            return True
        try:
            task = self.prepare_credit_note_output_task(credit_note_id)
        except Exception as exc:
            messagebox.showerror("Odobrenje", f"Dokument nije moguće pripremiti:\n{exc}", parent=self)
            return False

        def open_export(bundle: dict[str, Path]) -> None:
            open_path(bundle["pdf" if format_name == "pdf" else "xlsx"])

        return self.run_pdf_export(title="Priprema formalnog odobrenja", task=task, on_success=open_export)

    def invoice_output_path(self, invoice_id: int, format_name: str) -> Path:
        """Return the stable project archive path for a generated invoice copy."""
        invoice = self.db.invoice_export_payload(invoice_id)
        if not invoice:
            raise ValueError("Faktura ne postoji.")
        suffix = "pdf" if format_name == "pdf" else "xlsx"
        base = safe_filename(f"faktura_{invoice.get('invoice_number') or invoice_id}")
        return self.db.invoice_archive_dir(invoice_id) / f"{base}.{suffix}"

    def queue_invoice_output_export(self, invoice_id: int) -> bool:
        """Create each saved invoice's Excel/PDF copy in a single background queue."""
        try:
            task = self.prepare_invoice_output_task(invoice_id)
        except Exception:
            return False
        if self._auto_pdf_active_invoice_id == invoice_id:
            # A second save arrived while the first snapshot was exporting.
            self._auto_pdf_rerun_tasks[invoice_id] = task
        elif invoice_id in self._auto_pdf_tasks:
            self._auto_pdf_tasks[invoice_id] = task
        else:
            self._auto_pdf_tasks[invoice_id] = task
            self._auto_pdf_queue.append(invoice_id)
        self._start_next_auto_pdf_export()
        return True

    def _start_next_auto_pdf_export(self) -> None:
        if self._auto_pdf_active_invoice_id is not None or not self._auto_pdf_queue:
            return
        if self._pdf_export_active:
            self.after(250, self._start_next_auto_pdf_export)
            return
        invoice_id = self._auto_pdf_queue.pop(0)
        task = self._auto_pdf_tasks.pop(invoice_id, None)
        if task is None:
            self._start_next_auto_pdf_export()
            return
        self._auto_pdf_active_invoice_id = invoice_id

        def worker() -> None:
            try:
                result: tuple[bool, Any] = (True, task())
            except Exception as exc:
                result = (False, exc)
            try:
                self.after(0, lambda: self._finish_auto_pdf_export(invoice_id, result))
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="opsnest-auto-pdf-export", daemon=True).start()

    def _finish_auto_pdf_export(self, invoice_id: int, result: tuple[bool, Any]) -> None:
        self._auto_pdf_active_invoice_id = None
        retry_task = self._auto_pdf_rerun_tasks.pop(invoice_id, None)
        if retry_task is not None:
            self._auto_pdf_tasks[invoice_id] = retry_task
            self._auto_pdf_queue.append(invoice_id)
        success, value = result
        if not success:
            messagebox.showwarning(
                "Automatski PDF",
                "Faktura je sačuvana, ali PDF i Excel kopija nisu mogli automatski da se naprave. "
                f"Možete pokušati kroz PDF / štampa.\n\n{value}",
                parent=self,
            )
        self._start_next_auto_pdf_export()

    def open_or_generate_invoice_output(self, invoice_id: int, format_name: str) -> bool:
        """Open a ready project archive copy; only generate it when it is missing."""
        try:
            output = self.invoice_output_path(invoice_id, format_name)
        except Exception as exc:
            messagebox.showerror("Faktura", f"Dokument nije dostupan:\n{exc}", parent=self)
            return False
        if output.is_file() and output.stat().st_size > 0:
            open_path(output)
            return True
        try:
            task = self.prepare_invoice_output_task(invoice_id)
        except Exception as exc:
            messagebox.showerror("PDF / štampa", f"Dokument nije moguće pripremiti:\n{exc}", parent=self)
            return False

        def open_export(bundle: dict[str, Path]) -> None:
            open_path(bundle["pdf" if format_name == "pdf" else "xlsx"])

        return self.run_pdf_export(
            title="Priprema PDF-a fakture",
            task=task,
            on_success=open_export,
        )

    def run_pdf_export(
        self,
        *,
        title: str,
        task: Callable[[], Any],
        on_success: Callable[[Any], None],
    ) -> bool:
        """Run one native Excel PDF export without freezing Tkinter's event loop."""
        if self._pdf_export_active or self._auto_pdf_active_invoice_id is not None:
            messagebox.showinfo(
                "PDF je u pripremi",
                "Jedan PDF se već pravi u pozadini. Sačekajte da se taj postupak završi, pa otvorite fakturu ponovo.",
                parent=self,
            )
            return False
        self._pdf_export_active = True

        def finished() -> None:
            self._pdf_export_active = False

        def failed(exc: Exception) -> None:
            messagebox.showerror(
                "PDF / štampa",
                "PDF iz originalnog Excel šablona nije moguće napraviti:\n"
                f"{exc}",
                parent=self,
            )

        PdfExportProgressDialog(
            self,
            title=title,
            task=task,
            on_success=on_success,
            on_error=failed,
            on_finished=finished,
        )
        return True

    def archive_invoice_excel(self, invoice_id: int) -> Path:
        """Store a draft's editable Excel copy without delaying the user on PDF rendering."""
        invoice = self.db.invoice_export_payload(invoice_id)
        if not invoice:
            raise ValueError("Faktura ne postoji.")
        archive_dir = self.db.invoice_archive_dir(invoice_id)
        output = archive_dir / f"{safe_filename('faktura_' + str(invoice.get('invoice_number') or invoice_id))}.xlsx"
        template_path = self.db.invoice_template_path(int(invoice.get("invoice_template_id") or 0))
        return export_invoice_xlsx(invoice, output, template_path=template_path)


class DashboardTab(ttk.Frame):
    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master, style="App.TFrame")
        self.app = app
        today = date.today()
        self.period_from_var = tk.StringVar(value=today.replace(day=1).strftime("%d.%m.%Y"))
        self.period_to_var = tk.StringVar(value=today.strftime("%d.%m.%Y"))
        self.period_caption_var = tk.StringVar()
        self.card_vars: dict[str, tk.StringVar] = {
            "month_issued": tk.StringVar(),
            "month_paid": tk.StringVar(),
            "month_balance": tk.StringVar(),
            "overdue_total": tk.StringVar(),
            "invoice_count": tk.StringVar(),
            "month_turnover": tk.StringVar(),
            "month_vat": tk.StringVar(),
        }
        self._debtor_customers: dict[str, str] = {}
        self._debtor_invoice_ids: dict[str, int] = {}
        self._payment_invoice_ids: dict[str, int] = {}
        self._payment_ids: dict[str, int] = {}
        self._build()

    def _card(self, parent: ttk.Frame, title: str, var: tk.StringVar, row: int, col: int) -> None:
        card = ttk.Frame(parent, style="Panel.TFrame", relief="solid", borderwidth=1)
        card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", padx=12, pady=(10, 0))
        ttk.Label(card, textvariable=var, style="CardValue.TLabel").pack(anchor="w", padx=12, pady=(2, 12))

    def _build(self) -> None:
        root = ttk.Frame(self, style="App.TFrame")
        root.pack(fill="both", expand=True)
        period_bar = ttk.LabelFrame(root, text="Dashboard svih projekata", padding=10)
        period_bar.pack(fill="x", padx=6, pady=(6, 0))
        period_bar.columnconfigure(9, weight=1)
        ttk.Label(period_bar, text="Period od", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(period_bar, textvariable=self.period_from_var, width=14, style="Modern.TEntry").grid(row=0, column=1, sticky="w", padx=(0, 10))
        ttk.Label(period_bar, text="do", style="Field.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(period_bar, textvariable=self.period_to_var, width=14, style="Modern.TEntry").grid(row=0, column=3, sticky="w", padx=(0, 10))
        ttk.Button(period_bar, text="Primeni", style="Primary.TButton", command=self.apply_period).grid(row=0, column=4, sticky="w", padx=(0, 6))
        ttk.Button(period_bar, text="Ovaj mesec", command=self.set_current_month).grid(row=0, column=5, sticky="w", padx=3)
        ttk.Button(period_bar, text="Ova godina", command=self.set_current_year).grid(row=0, column=6, sticky="w", padx=3)
        ttk.Button(period_bar, text="Sve vreme", command=self.set_all_time).grid(row=0, column=7, sticky="w", padx=3)
        ttk.Button(period_bar, text="Operativni centar", command=self.open_daily_work_center).grid(row=0, column=8, sticky="w", padx=(12, 3))
        ttk.Button(period_bar, text="Finansijski savetnik", command=self.open_financial_advisor).grid(row=0, column=9, sticky="e", padx=(6, 0))
        ttk.Label(period_bar, textvariable=self.period_caption_var, style="Help.TLabel").grid(row=1, column=0, columnspan=10, sticky="w", pady=(8, 0))

        summary = ttk.Frame(root, style="App.TFrame")
        summary.pack(fill="x", padx=6, pady=6)
        for i in range(4):
            summary.columnconfigure(i, weight=1)
        self._card(summary, "Fakturisano u periodu", self.card_vars["month_issued"], 0, 0)
        self._card(summary, "Naplaćeno u periodu", self.card_vars["month_paid"], 0, 1)
        self._card(summary, "Preostalo za naplatu", self.card_vars["month_balance"], 0, 2)
        self._card(summary, "Dospelo u periodu", self.card_vars["overdue_total"], 0, 3)
        self._card(summary, "Broj izdatih faktura", self.card_vars["invoice_count"], 1, 0)
        self._card(summary, "Promet bez PDV-a", self.card_vars["month_turnover"], 1, 1)
        self._card(summary, "Obračunati PDV", self.card_vars["month_vat"], 1, 2)
        ttk.Frame(summary, style="App.TFrame").grid(row=1, column=3, sticky="nsew")

        lower = ttk.Frame(root, style="App.TFrame")
        lower.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        lower.columnconfigure(0, weight=1)
        lower.columnconfigure(1, weight=1)
        lower.rowconfigure(0, weight=1)

        debtors_frame = ttk.LabelFrame(lower, text="Najveći dužnici", padding=10)
        debtors_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.debtors_tree = ttk.Treeview(
            debtors_frame,
            columns=("customer", "invoices", "oldest_due", "balance"),
            show="headings",
            height=8,
        )
        setup_treeview_tree(self.debtors_tree)
        for col, title, width, anchor in [
            ("customer", "Kupac", 170, "w"),
            ("invoices", "Broj faktura", 95, "e"),
            ("oldest_due", "Najstariji rok", 110, "w"),
            ("balance", "Dug", 115, "e"),
        ]:
            self.debtors_tree.heading(col, text=title)
            self.debtors_tree.column(col, width=width, anchor=anchor)
        self.debtors_tree.pack(fill="both", expand=True)
        debtor_actions = ttk.Frame(debtors_frame, style="Panel.TFrame")
        debtor_actions.pack(fill="x", pady=(8, 0))
        ttk.Label(debtor_actions, text="Izaberite kupca ili fakturu bez kupca, pa je otvorite.", style="Help.TLabel").pack(side="left")
        ttk.Button(
            debtor_actions,
            text="Otvori sve fakture kupca",
            command=self.open_selected_debtor,
        ).pack(side="right")
        self.debtors_tree.bind("<Double-1>", lambda _event: self.open_selected_debtor())
        self.debtors_tree.bind("<Return>", lambda _event: self.open_selected_debtor())

        payments_frame = ttk.LabelFrame(lower, text="Poslednje uplate", padding=10)
        payments_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.payments_tree = ttk.Treeview(payments_frame, columns=("date", "invoice", "customer", "amount"), show="headings", height=8)
        setup_treeview_tree(self.payments_tree)
        for col, title, width, anchor in [
            ("date", "Datum", 85, "w"),
            ("invoice", "Faktura", 95, "w"),
            ("customer", "Kupac", 150, "w"),
            ("amount", "Iznos", 95, "e"),
        ]:
            self.payments_tree.heading(col, text=title)
            self.payments_tree.column(col, width=width, anchor=anchor)
        self.payments_tree.pack(fill="both", expand=True)
        payment_actions = ttk.Frame(payments_frame, style="Panel.TFrame")
        payment_actions.pack(fill="x", pady=(8, 0))
        ttk.Label(payment_actions, text="Dvoklik na uplatu otvara njenu fakturu.", style="Help.TLabel").pack(side="left")
        ttk.Button(
            payment_actions,
            text="Otvori fakturu",
            command=self.open_selected_payment_invoice,
        ).pack(side="right")
        ttk.Button(
            payment_actions,
            text="Obriši uplatu",
            command=self.delete_selected_payment,
        ).pack(side="right", padx=(0, 6))
        self.payments_tree.bind("<Double-1>", lambda _event: self.open_selected_payment_invoice())
        self.payments_tree.bind("<Return>", lambda _event: self.open_selected_payment_invoice())

    def set_current_month(self) -> None:
        today = date.today()
        self.period_from_var.set(today.replace(day=1).strftime("%d.%m.%Y"))
        self.period_to_var.set(today.strftime("%d.%m.%Y"))
        self.refresh()

    def set_current_year(self) -> None:
        today = date.today()
        self.period_from_var.set(today.replace(month=1, day=1).strftime("%d.%m.%Y"))
        self.period_to_var.set(today.strftime("%d.%m.%Y"))
        self.refresh()

    def set_all_time(self) -> None:
        self.period_from_var.set("")
        self.period_to_var.set("")
        self.refresh()

    def apply_period(self) -> None:
        for value in (self.period_from_var.get().strip(), self.period_to_var.get().strip()):
            if value and not parse_date(value):
                messagebox.showerror("Period", "Datum unesite u formatu dd.mm.gggg.")
                return
        self.refresh()

    def open_daily_work_center(self) -> None:
        DailyWorkCenterDialog(self, self.app)

    def open_financial_advisor(self) -> None:
        from_value = self.period_from_var.get().strip()
        to_value = self.period_to_var.get().strip()
        stats = self.app.db.dashboard_stats(period_from=from_value, period_to=to_value)
        FinancialAdvisorDialog(self, self.app, stats, self.period_caption_var.get())

    def _selected_debtor_customer(self) -> str | None:
        selection = self.debtors_tree.selection()
        if not selection:
            return None
        return self._debtor_customers.get(selection[0])

    def _selected_debtor_invoice_id(self) -> int | None:
        selection = self.debtors_tree.selection()
        if not selection:
            return None
        return self._debtor_invoice_ids.get(selection[0])

    def open_selected_debtor(self) -> None:
        customer_name = self._selected_debtor_customer()
        if customer_name:
            CustomerInvoicesDialog(self, self.app, customer_name)
            return
        invoice_id = self._selected_debtor_invoice_id()
        if invoice_id:
            self.app.open_invoice_editor(invoice_id)
            return
        if not customer_name:
            messagebox.showinfo("Dužnici", "Izaberite kupca iz liste dužnika.", parent=self.winfo_toplevel())
            return

    def _selected_payment_invoice_id(self) -> int | None:
        selection = self.payments_tree.selection()
        if not selection:
            return None
        return self._payment_invoice_ids.get(selection[0])

    def open_selected_payment_invoice(self) -> None:
        invoice_id = self._selected_payment_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Poslednje uplate", "Izaberite uplatu iz liste.", parent=self.winfo_toplevel())
            return
        self.app.open_invoice_editor(invoice_id)

    def delete_selected_payment(self) -> None:
        selection = self.payments_tree.selection()
        payment_id = self._payment_ids.get(selection[0]) if selection else None
        if not payment_id:
            messagebox.showinfo("Poslednje uplate", "Izaberite uplatu iz liste.", parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno(
            "Obriši uplatu",
            "Obrisati izabranu uplatu? Ako je nastala iz bankovnog izvoda, bankovna stavka će se vratiti na proveru.",
            parent=self.winfo_toplevel(),
        ):
            return
        try:
            self.app.db.delete_payment(payment_id)
        except ValueError as exc:
            messagebox.showerror("Uplata", str(exc), parent=self.winfo_toplevel())
            return
        self.app.refresh_all()

    def refresh(self) -> None:
        from_value = self.period_from_var.get().strip()
        to_value = self.period_to_var.get().strip()
        stats = self.app.db.dashboard_stats(period_from=from_value, period_to=to_value)
        if from_value or to_value:
            self.period_caption_var.set(
                tr("Svi projekti zajedno | Period: {from_date} - {to_date}").format(
                    from_date=from_value or tr("početak"),
                    to_date=to_value or tr("danas"),
                )
            )
        else:
            self.period_caption_var.set(tr("Svi projekti zajedno | Period: sve vreme"))
        self.card_vars["month_issued"].set(fmt_money(stats["month_issued"]))
        self.card_vars["month_paid"].set(fmt_money(stats["month_paid"]))
        self.card_vars["month_balance"].set(fmt_money(stats["month_balance"]))
        self.card_vars["overdue_total"].set(f"{fmt_money(stats['overdue_total'])} ({stats['overdue_count']})")
        self.card_vars["invoice_count"].set(str(stats["invoice_count"]))
        self.card_vars["month_turnover"].set(fmt_money(stats["month_turnover"]))
        self.card_vars["month_vat"].set(fmt_money(stats["month_vat"]))
        for tree in (self.debtors_tree, self.payments_tree):
            for item in tree.get_children():
                tree.delete(item)
        self._debtor_customers.clear()
        self._debtor_invoice_ids.clear()
        self._payment_invoice_ids.clear()
        self._payment_ids.clear()
        for row in stats["debtors"]:
            idx = len(self.debtors_tree.get_children())
            item_id = f"debtor-{idx}"
            self.debtors_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    row["customer_name"],
                    row["invoice_count"],
                    display_date(row.get("oldest_due_date")),
                    fmt_money(row["balance"]),
                ),
                tags=(tree_row_tag(idx),),
            )
            self._debtor_customers[item_id] = str(row.get("customer_name") or "")
            self._debtor_invoice_ids[item_id] = int(row.get("direct_invoice_id") or 0)
        for row in stats["recent_payments"]:
            idx = len(self.payments_tree.get_children())
            item_id = f"payment-{idx}"
            self.payments_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    display_date(row["payment_date"]),
                    row["invoice_number"],
                    row["customer_name"],
                    fmt_money(row["amount"]),
                ),
                tags=(tree_row_tag(idx),),
            )
            self._payment_invoice_ids[item_id] = int(row.get("invoice_id") or 0)
            self._payment_ids[item_id] = int(row.get("payment_id") or 0)


class FinancialControlTab(ttk.Frame):
    """Company-wide owner finance.  It complements, rather than replaces, an accountant's ledger."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master, style="App.TFrame")
        self.app = app
        self.balance_var, self.payables_var, self.ready_to_pay_var, self.profit_var, self.flow_var = (tk.StringVar() for _ in range(5))
        self.approval_policy_var = tk.StringVar()
        self.cash_horizon = tk.IntVar(value=30)
        self._build()

    def _card(self, parent: tk.Widget, title: str, variable: tk.StringVar) -> None:
        card = ttk.Frame(parent, style="Panel.TFrame", relief="solid", borderwidth=1, padding=10)
        card.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=variable, style="CardValue.TLabel", wraplength=260).pack(anchor="w", pady=(4, 0))

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=12)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(3, weight=1)
        outer.columnconfigure(0, weight=1)
        intro = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(intro, text=tr("Finansijski centar firme"), style="Section.TLabel").pack(anchor="w")
        ttk.Label(intro, text=tr("Operativni pregled za vlasnika: obaveze, novac i plan. Nije zamena za lokalno zakonsko knjigovodstvo ili poresku prijavu."), style="Help.TLabel", wraplength=1200).pack(anchor="w", pady=(3, 0))
        ttk.Label(intro, textvariable=self.approval_policy_var, style="Help.TLabel", wraplength=1200).pack(anchor="w", pady=(3, 0))
        actions = ttk.Frame(outer, style="App.TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        primary_actions = ttk.Frame(actions, style="App.TFrame")
        primary_actions.pack(fill="x")
        review_actions = ttk.Frame(actions, style="App.TFrame")
        review_actions.pack(fill="x", pady=(5, 0))
        for text, command, primary in (
            ("Novi dobavljač", self.new_vendor, False), ("Nova obaveza", self.new_bill, True),
            ("Ponavljajući trošak", self.new_recurring, False), ("Račun / kasa", self.new_cash_account, False),
        ):
            ttk.Button(primary_actions, text=tr(text), command=command, style="Primary.TButton" if primary else "TButton").pack(side="left", padx=(0, 5))
        for text, command in (
            ("Otvori dokument", self.open_bill_attachment), ("Odobri obavezu", self.approve_bill), ("Odbij obavezu", self.reject_bill),
            ("Vrati na proveru", self.resubmit_bill), ("Komentari", self.open_bill_comments), ("Kontni plan i dnevnik", self.open_ledger),
            ("Mesečna kontrola", self.open_monthly_control), ("Zaključi period", self.new_period), ("Kreiraj dospele troškove", self.run_recurring),
            ("Izvezi audit", self.export_financial_audit),
        ):
            ttk.Button(review_actions, text=tr(text), command=command).pack(side="left", padx=(0, 5))
        ttk.Label(review_actions, text=tr("Cash-flow:"), style="Field.TLabel").pack(side="left", padx=(8, 3))
        for days in (7, 30, 90):
            ttk.Radiobutton(review_actions, text=f"{days} {tr('dana')}", value=days, variable=self.cash_horizon, command=self.refresh).pack(side="left", padx=2)
        ttk.Button(review_actions, text=tr("Osveži"), command=self.refresh).pack(side="right")
        metrics = ttk.Frame(outer, style="App.TFrame")
        metrics.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._card(metrics, tr("Početno stanje računa"), self.balance_var)
        self._card(metrics, tr("Otvorene obaveze"), self.payables_var)
        self._card(metrics, tr("Spremno za plaćanje"), self.ready_to_pay_var)
        self._card(metrics, tr("Rezultat firme"), self.profit_var)
        self._card(metrics, tr("Cash-flow izabranog horizonta"), self.flow_var)
        notebook = ttk.Notebook(outer)
        notebook.grid(row=3, column=0, sticky="nsew")
        self.payables_tree = self._tree(notebook, ("due", "vendor", "number", "project", "gross", "paid", "balance", "approval", "status"), (
            ("due", tr("Rok"), 95), ("vendor", tr("Dobavljač"), 190), ("number", tr("Broj"), 120), ("project", tr("Projekat"), 160),
            ("gross", tr("Ukupno"), 115), ("paid", tr("Plaćeno"), 115), ("balance", tr("Za plaćanje"), 125), ("approval", tr("Odobrenje"), 110), ("status", tr("Status"), 100),
        ))
        self.cash_tree = self._tree(notebook, ("currency", "opening", "inflow", "outflow", "closing"), (
            ("currency", tr("Valuta"), 90), ("opening", tr("Početno"), 160), ("inflow", tr("Očekivani prilivi"), 180), ("outflow", tr("Očekivani odlivi"), 180), ("closing", tr("Procena na kraju"), 180),
        ))
        self.payment_plan_tree = self._tree(notebook, ("priority", "due", "vendor", "number", "project", "balance", "status"), (
            ("priority", tr("Prioritet"), 145), ("due", tr("Rok"), 95), ("vendor", tr("Dobavljač"), 220), ("number", tr("Broj"), 125),
            ("project", tr("Projekat"), 170), ("balance", tr("Za plaćanje"), 135), ("status", tr("Status"), 130),
        ))
        self.pnl_tree = self._tree(notebook, ("currency", "income", "expense", "profit", "output_vat", "input_vat", "vat_payable"), (
            ("currency", tr("Valuta"), 90), ("income", tr("Prihod bez PDV-a"), 165), ("expense", tr("Troškovi bez PDV-a"), 175),
            ("profit", tr("Rezultat"), 145), ("output_vat", tr("Izlazni PDV"), 145), ("input_vat", tr("Ulazni PDV"), 145), ("vat_payable", tr("Neto PDV"), 145),
        ))
        self.audit_tree = self._tree(notebook, ("time", "record", "action", "details"), (
            ("time", tr("Vreme"), 150), ("record", tr("Stavka"), 180), ("action", tr("Akcija"), 170), ("details", tr("Detalji"), 560),
        ))
        notebook.add(self.payables_tree.master, text=tr("Obaveze dobavljačima"))
        notebook.add(self.payment_plan_tree.master, text=tr("Plan plaćanja"))
        notebook.add(self.cash_tree.master, text=tr("Cash-flow prognoza"))
        notebook.add(self.pnl_tree.master, text=tr("P&L i PDV firme"))
        notebook.add(self.audit_tree.master, text=tr("Finansijski audit"))
        self.payables_tree.bind("<Double-1>", lambda _event: self.edit_bill())

    def _tree(self, parent: ttk.Notebook, columns: tuple[str, ...], headers: tuple[tuple[str, str, int], ...]) -> ttk.Treeview:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=8)
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        setup_treeview_tree(tree)
        for key, label, width in headers:
            tree.heading(key, text=label); tree.column(key, width=width, anchor="e" if key in {"gross", "paid", "balance", "opening", "inflow", "outflow", "closing"} else "w", stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew"); scroll.grid(row=0, column=1, sticky="ns")
        return tree

    def refresh(self) -> None:
        company = self.app.company
        currency = company.get("default_currency") or DEFAULT_CURRENCY
        owner_ceiling = money_round(company.get("vendor_bill_owner_approval_threshold") or 0)
        if owner_ceiling > 0:
            self.approval_policy_var.set(
                f"Kontrola odobrenja: administrator do {fmt_money(owner_ceiling, currency)}; "
                "na/iznad limita i svaka strana valuta zahtevaju vlasnika."
            )
        else:
            self.approval_policy_var.set(
                "Kontrola odobrenja: limit vlasnika nije podešen. Vlasnik može uključiti limit u Podacima firme."
            )
        summary = self.app.db.company_financial_summary()
        forecast = self.app.db.cash_flow_forecast(days=int(self.cash_horizon.get() or 30))
        default_pnl = summary.get("currencies", {}).get(currency, {})
        default_flow = forecast.get("currencies", {}).get(currency, {})
        opening = sum((money_round(row.get("opening_balance")) for row in self.app.db.list_cash_accounts() if row.get("currency") == currency), Decimal("0"))
        self.balance_var.set(fmt_money(opening, currency))
        open_bills = self.app.db.list_vendor_bills(include_paid=False)
        payment_plan = self.app.db.vendor_payment_plan(days=7)
        same_currency = sum((money_round(row.get("balance_amount")) for row in open_bills if row.get("currency") == currency), Decimal("0"))
        foreign = len([row for row in open_bills if row.get("currency") != currency])
        self.payables_var.set(f"{fmt_money(same_currency, currency)}" + (f" | +{foreign} {tr('drugih valuta')}" if foreign else ""))
        ready_amount = money_round(payment_plan.get("totals", {}).get(currency, 0))
        ready_foreign = len([cur for cur, amount in payment_plan.get("totals", {}).items() if cur != currency and money_round(amount) > 0])
        self.ready_to_pay_var.set(f"{fmt_money(ready_amount, currency)}" + (f" | +{ready_foreign} {tr('drugih valuta')}" if ready_foreign else ""))
        self.profit_var.set(fmt_money(default_pnl.get("profit_net", 0), currency) if default_pnl else tr("Nema stavki"))
        self.flow_var.set(fmt_money(default_flow.get("closing_balance", 0), currency) if default_flow else tr("Nema prognoze"))
        for tree in (self.payables_tree, self.payment_plan_tree, self.cash_tree, self.pnl_tree, self.audit_tree):
            for item in tree.get_children(): tree.delete(item)
        for index, bill in enumerate(open_bills):
            cur = bill.get("currency") or currency
            approval_code = str(bill.get("approval_status") or "approved")
            approval = {"pending": tr("Na proveri"), "approved": tr("Odobrena"), "rejected": tr("Odbijena")}.get(approval_code, approval_code)
            self.payables_tree.insert("", "end", iid=str(bill["id"]), values=(display_date(bill.get("due_date")), bill.get("vendor_name") or "", bill.get("bill_number") or "", bill.get("project_name") or "", fmt_money(bill.get("gross_amount"),cur), fmt_money(bill.get("paid_amount"),cur), fmt_money(bill.get("balance_amount"),cur), approval, bill.get("status") or "open"), tags=(tree_row_tag(index),))
        payment_labels = {
            "overdue": tr("Kasni"), "today": tr("Dospelo danas"), "next_7_days": tr("U narednih 7 dana"), "later": tr("Kasnije"), "without_due_date": tr("Bez roka"),
        }
        for index, bill in enumerate(payment_plan.get("ready", [])):
            cur = bill.get("currency") or currency
            bucket = str(bill.get("payment_bucket") or "without_due_date")
            days_until = bill.get("days_until_due")
            priority = payment_labels.get(bucket, bucket)
            if bucket == "overdue" and days_until is not None:
                priority = f"{priority}: {abs(int(days_until))} {tr('dana')}"
            self.payment_plan_tree.insert("", "end", iid=str(bill["id"]), values=(priority, display_date(bill.get("due_date")), bill.get("vendor_name") or "", bill.get("bill_number") or "", bill.get("project_name") or "", fmt_money(bill.get("balance_amount"),cur), bill.get("status") or "open"), tags=(tree_row_tag(index),))
        for index, (cur, values) in enumerate(forecast.get("currencies", {}).items()):
            self.cash_tree.insert("", "end", values=(cur, fmt_money(values.get("opening_balance",0),cur), fmt_money(values.get("inflows",0),cur), fmt_money(values.get("outflows",0),cur), fmt_money(values.get("closing_balance",0),cur)), tags=(tree_row_tag(index),))
        for index, (cur, values) in enumerate(summary.get("currencies", {}).items()):
            self.pnl_tree.insert(
                "", "end", values=(
                    cur, fmt_money(values.get("income_net", 0), cur), fmt_money(values.get("expense_net", 0), cur),
                    fmt_money(values.get("profit_net", 0), cur), fmt_money(values.get("income_vat", 0), cur),
                    fmt_money(values.get("expense_vat", 0), cur), fmt_money(values.get("vat_payable", 0), cur),
                ), tags=(tree_row_tag(index),),
            )
        for index, event in enumerate(self.app.db.list_financial_audit(limit=150)):
            self.audit_tree.insert("", "end", values=(event.get("created_at"), f"{event.get('record_type')} #{event.get('record_id')}", event.get("action_code"), event.get("details")), tags=(tree_row_tag(index),))

    def new_vendor(self) -> None:
        if self.app.require_team_permission({"owner", "administrator", "accountant"}, "unos dobavljača", parent=self): FinancialRecordDialog(self, self.app, "vendor", on_saved=self.refresh)
    def new_bill(self) -> None:
        if self.app.require_team_permission({"owner", "administrator", "accountant", "project_manager"}, "unos obaveze dobavljača", parent=self): FinancialRecordDialog(self, self.app, "bill", on_saved=self.refresh)
    def new_cash_account(self) -> None:
        if self.app.require_team_permission({"owner", "administrator", "accountant"}, "upravljanje računom ili kasom", parent=self): FinancialRecordDialog(self, self.app, "cash", on_saved=self.refresh)
    def new_recurring(self) -> None:
        if self.app.require_team_permission({"owner", "administrator", "accountant"}, "ponavljajući trošak", parent=self): FinancialRecordDialog(self, self.app, "recurring", on_saved=self.refresh)
    def new_period(self) -> None:
        if self.app.require_team_permission({"owner", "administrator"}, "zaključavanje obračunskog perioda", parent=self): FinancialRecordDialog(self, self.app, "period", on_saved=self.refresh)
    def open_monthly_control(self) -> None: MonthlyControlChecklistDialog(self, self.app, on_changed=self.refresh)
    def open_ledger(self) -> None: LedgerDialog(self, self.app, on_saved=self.refresh)
    def export_financial_audit(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "accountant"}, "izvoz finansijskog audita", parent=self):
            return
        exported_at = datetime.now()
        destination = filedialog.asksaveasfilename(
            parent=self,
            title=tr("Sačuvaj finansijski audit"),
            defaultextension=".csv",
            initialfile=f"OpsNest-finansijski-audit-{exported_at:%Y-%m-%d}.csv",
            filetypes=[(tr("CSV dokument"), "*.csv")],
        )
        if not destination:
            return
        rows = self.app.db.list_financial_audit(limit=100000)
        chain = self.app.db.verify_financial_audit_chain()
        if not chain["ok"]:
            messagebox.showerror(
                tr("Finansijski audit"),
                f"Izvoz je zaustavljen: {chain['detail']} Ne zaključavajte period i prijavite incident administratoru.",
                parent=self,
            )
            return
        output = Path(destination)
        try:
            with output.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(["Vreme", "Vrsta zapisa", "ID zapisa", "Akcija", "Detalji"])
                for row in rows:
                    writer.writerow((row.get("created_at") or "", row.get("record_type") or "", row.get("record_id") or "", row.get("action_code") or "", row.get("details") or ""))
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            manifest = output.with_name(f"{output.name}.sha256.txt")
            manifest.write_text(
                f"SHA-256  {output.name}\n{digest}\nIzvezeno: {exported_at.isoformat(timespec='seconds')}\nStavki: {len(rows)}\nAudit lanac: {chain['last_hash'] or '-'}\n",
                encoding="utf-8",
            )
            self.app.db.record_financial_audit_export(self.app.active_team_member_name(), len(rows))
        except OSError as exc:
            messagebox.showerror(tr("Finansijski audit"), f"{tr('Izvoz nije uspeo')}:\n{exc}", parent=self)
            return
        self.refresh()
        messagebox.showinfo(
            tr("Finansijski audit"),
            tr("Audit je izvezen u CSV zajedno sa SHA-256 kontrolnim fajlom.") + f"\n\n{output}\n{manifest}",
            parent=self,
        )
    def approve_bill(self) -> None:
        selected = self.payables_tree.selection()
        if not selected:
            messagebox.showinfo("Odobravanje obaveze", "Izaberite obavezu iz liste.", parent=self)
            return
        if not self.app.require_team_permission({"owner", "administrator"}, "odobravanje obaveze dobavljača", parent=self):
            return
        bill_id = int(selected[0])
        bill = self.app.db.get_vendor_bill(bill_id)
        if str(bill.get("approval_status") or "approved") != "pending":
            messagebox.showinfo("Odobravanje obaveze", "Izabrana obaveza je već odobrena.", parent=self)
            return
        if not messagebox.askyesno("Odobravanje obaveze", f"Odobriti obavezu {bill.get('bill_number') or '#'+str(bill_id)} za plaćanje?", parent=self):
            return
        try:
            self.app.db.approve_vendor_bill(
                bill_id,
                self.app.active_team_member_name(),
                approver_role=self.app.active_team_role(),
            )
        except ValueError as exc:
            messagebox.showerror("Odobravanje obaveze", str(exc), parent=self)
            return
        self.app.refresh_all()
        self.refresh()
    def reject_bill(self) -> None:
        selected = self.payables_tree.selection()
        if not selected:
            messagebox.showinfo("Odbijanje obaveze", "Izaberite obavezu iz liste.", parent=self)
            return
        if not self.app.require_team_permission({"owner", "administrator"}, "odbijanje obaveze dobavljača", parent=self):
            return
        bill_id = int(selected[0])
        bill = self.app.db.get_vendor_bill(bill_id)
        if str(bill.get("approval_status") or "approved") != "pending":
            messagebox.showinfo("Odbijanje obaveze", "Može se odbiti samo obaveza koja je na proveri.", parent=self)
            return
        reason = simpledialog.askstring("Odbijanje obaveze", "Napišite razlog odbijanja:", parent=self)
        if reason is None:
            return
        try:
            self.app.db.reject_vendor_bill(bill_id, self.app.active_team_member_name(), reason)
        except ValueError as exc:
            messagebox.showerror("Odbijanje obaveze", str(exc), parent=self)
            return
        self.app.refresh_all(); self.refresh()
    def resubmit_bill(self) -> None:
        selected = self.payables_tree.selection()
        if not selected:
            messagebox.showinfo("Provera obaveze", "Izaberite obavezu iz liste.", parent=self)
            return
        if not self.app.require_team_permission({"owner", "administrator", "project_manager", "accountant"}, "slanje obaveze na proveru", parent=self):
            return
        bill_id = int(selected[0])
        note = simpledialog.askstring("Provera obaveze", "Kratka napomena za vlasnika (nije obavezna):", parent=self)
        if note is None:
            return
        try:
            self.app.db.resubmit_vendor_bill(bill_id, self.app.active_team_member_name(), note)
        except ValueError as exc:
            messagebox.showerror("Provera obaveze", str(exc), parent=self)
            return
        self.app.refresh_all(); self.refresh()
    def open_bill_comments(self) -> None:
        selected = self.payables_tree.selection()
        if not selected:
            messagebox.showinfo("Komentari obaveze", "Izaberite obavezu iz liste.", parent=self)
            return
        VendorBillCommentsDialog(self, self.app, int(selected[0]), on_saved=self.refresh)
    def edit_bill(self) -> None:
        selected = self.payables_tree.selection()
        if not selected:
            return
        if self.app.require_team_permission({"owner", "administrator", "accountant", "project_manager"}, "izmenu obaveze dobavljača", parent=self):
            FinancialRecordDialog(self, self.app, "bill", record_id=int(selected[0]), on_saved=self.refresh)
    def open_bill_attachment(self) -> None:
        selected = self.payables_tree.selection()
        if not selected:
            messagebox.showinfo("Dokument dobavljača", "Izaberite obavezu iz liste.", parent=self)
            return
        bill = self.app.db.get_vendor_bill(int(selected[0]))
        path = Path(str(bill.get("attachment_path") or ""))
        if not path.is_file():
            messagebox.showinfo("Dokument dobavljača", "Za ovu obavezu još nema priloženog dokumenta.", parent=self)
            return
        open_path(path)
    def run_recurring(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "accountant"}, "kreiranje dospelih troškova", parent=self):
            return
        try:
            created = self.app.db.run_due_recurring_expenses()
            self.refresh(); messagebox.showinfo("Ponavljajući troškovi", f"Kreirano novih obaveza: {created}.", parent=self)
        except ValueError as exc: messagebox.showerror("Ponavljajući troškovi", str(exc), parent=self)


class VendorBillCommentsDialog(tk.Toplevel):
    """Reviewable conversation trail for a supplier liability and its approval."""

    def __init__(self, parent: tk.Widget, app: MainApp, bill_id: int, *, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app, self.bill_id, self.on_saved = app, int(bill_id), on_saved
        bill = app.db.get_vendor_bill(self.bill_id)
        self.title(f"Komentari obaveze — {bill.get('bill_number') or '#'+str(self.bill_id)}")
        self.configure(background=BG)
        self.comment_text: tk.Text
        self._build(bill)
        self.refresh()
        center_window(self, 760, 520)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

    def _build(self, bill: dict[str, Any]) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(2, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text=f"{bill.get('vendor_name') or tr('Dobavljač')} · {bill.get('bill_number') or '#'+str(self.bill_id)}", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        rejection = str(bill.get("rejection_reason") or "").strip()
        if rejection:
            ttk.Label(outer, text=f"{tr('Poslednji razlog odbijanja')}: {rejection}", style="Help.TLabel", wraplength=700).grid(row=1, column=0, sticky="w", pady=(3, 8))
        frame = ttk.Frame(outer, style="Panel.TFrame", padding=8)
        frame.grid(row=2, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=("time", "author", "type", "comment"), show="headings")
        setup_treeview_tree(self.tree)
        for key, title, width in (("time", tr("Vreme"), 140), ("author", tr("Korisnik"), 145), ("type", tr("Akcija"), 105), ("comment", tr("Komentar"), 420)):
            self.tree.heading(key, text=title); self.tree.column(key, width=width, anchor="w", stretch=key == "comment")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        entry = ttk.LabelFrame(outer, text=tr("Novi komentar"), padding=8)
        entry.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        entry.columnconfigure(0, weight=1)
        self.comment_text = tk.Text(entry, height=4, wrap="word", font=("Segoe UI", 10))
        self.comment_text.grid(row=0, column=0, sticky="ew")
        actions = ttk.Frame(entry, style="App.TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        ttk.Button(actions, text=tr("Dodaj komentar"), style="Primary.TButton", command=self.add_comment).pack(side="left")
        ttk.Button(actions, text=tr("Zatvori"), command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        labels = {"comment": tr("Komentar"), "approved": tr("Odobreno"), "rejected": tr("Odbijeno"), "resubmitted": tr("Vraćeno na proveru")}
        for index, row in enumerate(self.app.db.list_vendor_bill_comments(self.bill_id)):
            self.tree.insert("", "end", values=(row.get("created_at"), row.get("author_name"), labels.get(row.get("event_type"), row.get("event_type")), row.get("comment_text")), tags=(tree_row_tag(index),))

    def add_comment(self) -> None:
        text = self.comment_text.get("1.0", "end").strip()
        try:
            self.app.db.add_vendor_bill_comment(self.bill_id, self.app.active_team_member_name(), text)
        except ValueError as exc:
            messagebox.showerror(tr("Komentari obaveze"), str(exc), parent=self)
            return
        self.comment_text.delete("1.0", "end")
        self.refresh()
        self.app.refresh_all()
        if self.on_saved:
            self.on_saved()


class FinancialRecordDialog(tk.Toplevel):
    """Small, explicit forms keep operational entries reviewable by non-accountants."""
    def __init__(self, parent: tk.Widget, app: MainApp, kind: str, *, record_id: int = 0, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent); self.app, self.kind, self.record_id, self.on_saved = app, kind, record_id, on_saved
        self.title(tr({"vendor":"Dobavljač", "bill":"Obaveza dobavljača", "cash":"Račun ili kasa", "recurring":"Ponavljajući trošak", "period":"Obračunski period"}[kind])); self.configure(background=BG)
        self.vars: dict[str, tk.StringVar] = {}; self._build(); center_window(self, 760, 600); self.transient(parent.winfo_toplevel()); self.grab_set()

    def _field(self, parent: tk.Widget, row: int, key: str, label: str, value: Any = "", *, combo: tuple[str, ...] = ()) -> None:
        self.vars[key] = tk.StringVar(value=str(value or "")); ttk.Label(parent, text=tr(label), style="Field.TLabel").grid(row=row,column=0,sticky="w",padx=(0,8),pady=4)
        widget = ttk.Combobox(parent,textvariable=self.vars[key],values=combo,state="readonly",style="Modern.TCombobox") if combo else ttk.Entry(parent,textvariable=self.vars[key],style="Modern.TEntry")
        widget.grid(row=row,column=1,sticky="ew",pady=4)

    def _build(self) -> None:
        outer=ttk.Frame(self,style="App.TFrame",padding=14);outer.pack(fill="both",expand=True);outer.columnconfigure(1,weight=1)
        db=self.app.db; company=self.app.company; default_currency=company.get("default_currency") or DEFAULT_CURRENCY
        if self.kind=="vendor":
            data=db.get_vendor(self.record_id) if self.record_id else {}; fields=(("name","Naziv",data.get("name")),("tax_id","Matični / poreski broj",data.get("tax_id")),("vat_number","PDV broj",data.get("vat_number")),("email","E-mail",data.get("email")),("iban","IBAN",data.get("iban")),("payment_term_days","Podrazumevani rok (dani)",data.get("payment_term_days") or 14),("note","Napomena",data.get("note")))
        elif self.kind in {"bill","recurring"}:
            data=db.get_vendor_bill(self.record_id) if self.kind=="bill" and self.record_id else {}; vendors={f"{v['name']} [{v['id']}]":v['id'] for v in db.list_vendors()}; projects={"":0,**{f"{p['name']} [{p['id']}]":p['id'] for p in db.list_projects()}}; self.vendor_map,self.project_map=vendors,projects
            current_vendor=next((name for name,value in vendors.items() if value==data.get("vendor_id")),""); current_project=next((name for name,value in projects.items() if value==data.get("project_id")),"")
            fields=(("vendor","Dobavljač",current_vendor,"combo",tuple(vendors)),("project","Projekat",current_project,"combo",tuple(projects)),("name_or_number","Naziv" if self.kind=="recurring" else "Broj računa",data.get("bill_number")),("bill_date","Datum računa / početak",data.get("bill_date") or today_iso()),("due_date","Rok / sledeće kreiranje",data.get("due_date") or today_iso()),("net_amount","Iznos bez PDV",data.get("net_amount")),("vat_rate","PDV %",float(decimal_from(data.get("vat_rate"))*100) if data else 20),("currency","Valuta",data.get("currency") or default_currency,"combo",SUPPORTED_CURRENCIES),("category","Kategorija",data.get("category") or "Ostali troškovi","combo",tuple(PROJECT_COST_GROUPS)),("description","Opis",data.get("description") or data.get("name")),("interval","Ponavljanje (meseci)","1"))
        elif self.kind=="cash":
            fields=(("name","Naziv računa/kase",""),("account_type","Tip","bank","combo",("bank","cash")),("currency","Valuta",default_currency,"combo",SUPPORTED_CURRENCIES),("opening_balance","Početno stanje","0"),("opening_date","Datum početnog stanja",today_iso()),("iban_last4","Poslednje 4 cifre IBAN-a",""),("note","Napomena",""))
        else:
            fields=(("period_from","Početak perioda",today_iso()),("period_to","Kraj perioda",today_iso()),("status","Status","closed","combo",("open","closed")),("note","Napomena",""))
        for row, item in enumerate(fields):
            key,label,value,*extra=item; self._field(outer,row,key,label,value,combo=extra[1] if extra else ())
        extra_row = len(fields)
        if self.kind == "bill":
            self.vars["attachment_path"] = tk.StringVar(value=str(data.get("attachment_path") or ""))
            ttk.Label(outer, text=tr("Originalni dokument"), style="Field.TLabel").grid(row=extra_row, column=0, sticky="w", padx=(0, 8), pady=4)
            attachment = ttk.Frame(outer, style="App.TFrame"); attachment.grid(row=extra_row, column=1, sticky="ew", pady=4); attachment.columnconfigure(0, weight=1)
            ttk.Entry(attachment, textvariable=self.vars["attachment_path"], style="Modern.TEntry").grid(row=0, column=0, sticky="ew")
            ttk.Button(attachment, text=tr("Izaberi"), command=self.browse_attachment).grid(row=0, column=1, padx=(6, 0))
            ttk.Button(attachment, text=tr("Otvori"), command=self.open_attachment).grid(row=0, column=2, padx=(4, 0))
            extra_row += 1
        help_text = "Datumi: gggg-mm-dd. Zaključavanje sprečava nove operativne stavke sa tim datumom."
        if self.kind == "bill":
            help_text += " Obaveza ne može biti odobrena ni plaćena bez originalnog dokumenta ili povezanog ulaznog računa projekta."
        ttk.Label(outer,text=tr(help_text),style="Help.TLabel",wraplength=650).grid(row=extra_row,column=0,columnspan=2,sticky="w",pady=(8,12))
        ttk.Button(outer,text=tr("Sačuvaj"),style="Primary.TButton",command=self.save).grid(row=extra_row+1,column=0,sticky="w");ttk.Button(outer,text=tr("Otkaži"),command=self.destroy).grid(row=extra_row+1,column=1,sticky="e")

    def browse_attachment(self) -> None:
        path = filedialog.askopenfilename(parent=self, title=tr("Izaberite originalni račun ili dokument"), filetypes=[("Dokumenti", "*.pdf *.png *.jpg *.jpeg *.webp *.xlsx *.docx"), ("Sve datoteke", "*.*")])
        if path:
            self.vars["attachment_path"].set(path)

    def open_attachment(self) -> None:
        path = Path(self.vars.get("attachment_path", tk.StringVar()).get())
        if path.is_file():
            open_path(path)
        else:
            messagebox.showinfo(self.title(), tr("Najpre izaberite postojeći dokument."), parent=self)

    def save(self) -> None:
        permissions = {
            "vendor": ({"owner", "administrator", "accountant"}, "čuvanje dobavljača"),
            "bill": ({"owner", "administrator", "accountant", "project_manager"}, "čuvanje obaveze dobavljača"),
            "cash": ({"owner", "administrator", "accountant"}, "čuvanje računa ili kase"),
            "recurring": ({"owner", "administrator", "accountant"}, "čuvanje ponavljajućeg troška"),
            "period": ({"owner", "administrator"}, "zaključavanje obračunskog perioda"),
        }
        allowed, action = permissions[self.kind]
        if not self.app.require_team_permission(allowed, action, parent=self):
            return
        try:
            if self.kind=="vendor": record=self.app.db.save_vendor({"id":self.record_id,**{key:var.get() for key,var in self.vars.items()}})
            elif self.kind=="cash": record=self.app.db.save_cash_account({"id":self.record_id, **{key:var.get() for key,var in self.vars.items()}})
            elif self.kind=="period":
                period_data = {key: var.get() for key, var in self.vars.items()}
                if str(period_data.get("status") or "").lower() == "closed":
                    period_data["closed_by"] = self.app.active_team_member_name()
                record = self.app.db.save_accounting_period(period_data)
            else:
                vendor_id=self.vendor_map.get(self.vars["vendor"].get());
                if self.kind=="bill":
                    bill_data={"id":self.record_id,"vendor_id":vendor_id,"project_id":self.project_map.get(self.vars["project"].get()),"bill_number":self.vars["name_or_number"].get(),"bill_date":self.vars["bill_date"].get(),"due_date":self.vars["due_date"].get(),"net_amount":self.vars["net_amount"].get(),"vat_rate":self.vars["vat_rate"].get(),"currency":self.vars["currency"].get(),"category":self.vars["category"].get(),"description":self.vars["description"].get()}
                    current_attachment = self.vars["attachment_path"].get().strip()
                    existing_bill = self.app.db.get_vendor_bill(self.record_id) if self.record_id else {}
                    existing_attachment = str(existing_bill.get("attachment_path") or "")
                    if existing_bill:
                        bill_data["approval_status"] = existing_bill.get("approval_status") or "approved"
                        bill_data["prepared_by_name"] = existing_bill.get("prepared_by_name") or ""
                        bill_data["approved_by_name"] = existing_bill.get("approved_by_name") or ""
                        bill_data["approved_at"] = existing_bill.get("approved_at") or ""
                    elif self.app.invoice_approval_enabled() and not self.app.is_owner_or_administrator():
                        bill_data["approval_status"] = "pending"
                        bill_data["prepared_by_name"] = self.app.active_team_member_name()
                    else:
                        bill_data["approval_status"] = "approved"
                        bill_data["prepared_by_name"] = self.app.active_team_member_name()
                        bill_data["approved_by_name"] = self.app.active_team_member_name()
                    bill_data["attachment_path"] = current_attachment or existing_attachment
                    record=self.app.db.save_vendor_bill(bill_data)
                    chosen_attachment = Path(current_attachment)
                    if chosen_attachment.is_file() and str(chosen_attachment) != existing_attachment:
                        bill_data["id"] = record
                        bill_data["attachment_path"] = self.app.db.archive_vendor_bill_attachment(record, chosen_attachment)
                        record=self.app.db.save_vendor_bill(bill_data)
                else: record=self.app.db.save_recurring_expense({"vendor_id":vendor_id,"project_id":self.project_map.get(self.vars["project"].get()),"name":self.vars["name_or_number"].get(),"next_run_date":self.vars["due_date"].get(),"net_amount":self.vars["net_amount"].get(),"vat_rate":self.vars["vat_rate"].get(),"currency":self.vars["currency"].get(),"category":self.vars["category"].get(),"interval_months":self.vars["interval"].get()})
        except (ValueError, sqlite3.Error) as exc: messagebox.showerror(self.title(),str(exc),parent=self);return
        if self.on_saved:self.on_saved()
        self.app.refresh_all();self.destroy()


class LedgerDialog(tk.Toplevel):
    """Small working-ledger surface; local statutory mappings stay opt-in."""
    def __init__(self, parent: tk.Widget, app: MainApp, *, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent); self.app, self.on_saved = app, on_saved
        self.title(tr("Kontni plan i dvostavni dnevnik")); self.configure(background=BG)
        self._build(); self.refresh(); center_window(self, 1140, 680); self.transient(parent.winfo_toplevel())

    def _tree(self, parent: tk.Widget, columns: tuple[str, ...], headings: tuple[tuple[str, str, int], ...]) -> ttk.Treeview:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=8); frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse"); setup_treeview_tree(tree)
        for key, label, width in headings: tree.heading(key, text=tr(label)); tree.column(key, width=width, anchor="e" if key in {"debit","credit","balance"} else "w", stretch=True)
        tree.pack(side="left", fill="both", expand=True); ttk.Scrollbar(frame, orient="vertical", command=tree.yview).pack(side="right", fill="y"); return tree

    def _build(self) -> None:
        outer=ttk.Frame(self,style="App.TFrame",padding=12); outer.pack(fill="both",expand=True)
        ttk.Label(outer,text=tr("Kontni plan i radni dvostavni dnevnik"),style="Section.TLabel").pack(anchor="w")
        ttk.Label(outer,text=tr("Ovo je kontrolni dnevnik za vlasnika i knjigovođu. Ne generiše zakonski kontni plan, bilans niti poresku prijavu bez lokalnog modula i potvrde knjigovođe."),style="Help.TLabel",wraplength=1050).pack(anchor="w",pady=(3,8))
        actions=ttk.Frame(outer,style="App.TFrame");actions.pack(fill="x",pady=(0,8))
        ttk.Button(actions,text=tr("Novo konto"),command=lambda: LedgerAccountDialog(self,self.app,on_saved=self.refresh)).pack(side="left")
        ttk.Button(actions,text=tr("Nova dvostavna stavka"),style="Primary.TButton",command=self.new_entry).pack(side="left",padx=6)
        ttk.Button(actions,text=tr("Pregled stavke"),command=self.open_selected).pack(side="left",padx=(8,0))
        ttk.Button(actions,text=tr("Proknjiži nacrt"),command=self.post_selected).pack(side="left",padx=(8,0))
        ttk.Button(actions,text=tr("Napravi korektivnu stavku"),command=self.reverse_selected).pack(side="left",padx=6)
        ttk.Button(actions,text=tr("Osveži"),command=self.refresh).pack(side="right")
        book=ttk.Notebook(outer);book.pack(fill="both",expand=True)
        self.accounts=self._tree(book,("code","name","type","active"),(("code","Šifra",110),("name","Naziv",380),("type","Vrsta",150),("active","Aktivno",90)))
        self.entries=self._tree(book,("date","status","reference","description","lines","currencies"),(("date","Datum",100),("status","Status",105),("reference","Referenca",140),("description","Opis",430),("lines","Stavki",75),("currencies","Valute",110)))
        self.balance=self._tree(book,("currency","code","name","type","debit","credit","balance"),(("currency","Valuta",75),("code","Konto",100),("name","Naziv",290),("type","Vrsta",120),("debit","Duguje",120),("credit","Potražuje",120),("balance","Saldo",120)))
        book.add(self.accounts.master,text=tr("Kontni plan"));book.add(self.entries.master,text=tr("Dnevnik"));book.add(self.balance.master,text=tr("Bruto bilans"))

    def _selected_entry(self) -> tuple[int, tuple[Any, ...]] | None:
        selection = self.entries.selection()
        if not selection:
            messagebox.showinfo(tr("Dnevnik"), "Najpre izaberite stavku u kartici Dnevnik.", parent=self)
            return None
        entry_id = self.entry_ids.get(selection[0])
        if not entry_id:
            messagebox.showerror(tr("Dnevnik"), "Izabrana stavka više nije dostupna. Osvežite pregled.", parent=self)
            return None
        return entry_id, tuple(self.entries.item(selection[0], "values"))

    def post_selected(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "accountant"}, "knjiženje dvostavne stavke", parent=self):
            return
        selected = self._selected_entry()
        if not selected:
            return
        entry_id, values = selected
        if len(values) < 2 or str(values[1]).lower() != "draft":
            messagebox.showinfo(tr("Dnevnik"), "Možete proknjižiti samo stavku sa statusom nacrt.", parent=self)
            return
        comment = simpledialog.askstring(
            tr("Proknjiži nacrt"),
            "Kratak komentar pregleda (opciono):",
            parent=self,
        )
        if not messagebox.askyesno(tr("Proknjiži nacrt"), "Potvrđujete da je dvostavna stavka proverena i spremna za knjiženje?", parent=self):
            return
        try:
            self.app.db.post_journal_entry(entry_id, posted_by=self.app.active_team_member_name(), comment=comment or "")
        except (ValueError, sqlite3.Error) as exc:
            messagebox.showerror(tr("Dnevnik"), str(exc), parent=self); return
        self.app.refresh_all(); self.refresh()
        messagebox.showinfo(tr("Dnevnik"), "Stavka je proknjižena i više se ne menja direktno.", parent=self)

    def open_selected(self) -> None:
        selected = self._selected_entry()
        if selected:
            JournalEntryReviewDialog(self, self.app, selected[0])

    def reverse_selected(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "accountant"}, "korektivno knjiženje", parent=self):
            return
        selected = self._selected_entry()
        if not selected:
            return
        entry_id, values = selected
        if len(values) < 2 or str(values[1]).lower() != "posted":
            messagebox.showinfo(tr("Dnevnik"), "Korektivna stavka se pravi samo za proknjiženu stavku.", parent=self)
            return
        reason = simpledialog.askstring(tr("Korektivna stavka"), "Razlog korekcije (obavezno):", parent=self)
        if not str(reason or "").strip():
            messagebox.showwarning(tr("Korektivna stavka"), "Korektivna stavka nije napravljena bez razloga.", parent=self); return
        if not messagebox.askyesno(tr("Korektivna stavka"), "Napraviti novi nacrt sa obrnutim duguje/potražuje iznosima? Izvorna stavka ostaje nepromenjena.", parent=self):
            return
        try:
            correction_id = self.app.db.create_reversing_journal_entry(
                entry_id,
                created_by=self.app.active_team_member_name(),
                reason=reason,
            )
        except (ValueError, sqlite3.Error) as exc:
            messagebox.showerror(tr("Korektivna stavka"), str(exc), parent=self); return
        self.app.refresh_all(); self.refresh()
        messagebox.showinfo(tr("Korektivna stavka"), f"Napravljen je korektivni nacrt #{correction_id}. Pregledajte ga i zatim proknjižite.", parent=self)

    def new_entry(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "accountant"}, "unos dvostavne stavke", parent=self):
            return
        if not self.app.db.list_ledger_accounts(active_only=True):
            messagebox.showinfo(
                tr("Kontni plan i dvostavni dnevnik"),
                "Najpre kliknite „Novo konto“ i unesite najmanje dva aktivna konta, na primer 1000 Banka i 6000 Troškovi. Zatim možete uneti dvostavnu stavku.",
                parent=self,
            )
            return
        LedgerEntryDialog(self, self.app, on_saved=self.refresh)

    def refresh(self) -> None:
        self.entry_ids: dict[str, int] = {}
        for tree in (self.accounts,self.entries,self.balance):
            for item in tree.get_children(): tree.delete(item)
        for index,row in enumerate(self.app.db.list_ledger_accounts()): self.accounts.insert("","end",values=(row.get("code"),row.get("name"),row.get("account_type"),"da" if row.get("active") else "ne"),tags=(tree_row_tag(index),))
        for index,row in enumerate(self.app.db.list_journal_entries()):
            iid=f"entry:{row.get('id')}"; self.entry_ids[iid]=int(row["id"])
            self.entries.insert("","end",iid=iid,values=(display_date(row.get("entry_date")),row.get("status"),row.get("reference"),row.get("description"),row.get("line_count"),row.get("currencies")),tags=(tree_row_tag(index),))
        for index,row in enumerate(self.app.db.ledger_trial_balance().get("rows", [])):
            cur=row.get("currency") or DEFAULT_CURRENCY; self.balance.insert("","end",values=(cur,row.get("code"),row.get("name"),row.get("account_type"),fmt_money(row.get("debit"),cur),fmt_money(row.get("credit"),cur),fmt_money(row.get("balance"),cur)),tags=(tree_row_tag(index),))
        if self.on_saved: self.on_saved()


class LedgerAccountDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, app: MainApp, *, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent);self.app,self.on_saved=app,on_saved;self.title("Novo konto");self.configure(background=BG);self.vars={key:tk.StringVar(value=value) for key,value in {"code":"","name":"","account_type":"expense","note":""}.items()};self._build();center_window(self,520,280);self.transient(parent.winfo_toplevel());self.grab_set()
    def _build(self) -> None:
        frame=ttk.Frame(self,style="App.TFrame",padding=14);frame.pack(fill="both",expand=True);frame.columnconfigure(1,weight=1)
        for row,(key,label,choices) in enumerate((("code","Šifra konta",()),("name","Naziv",()),("account_type","Vrsta",("asset","liability","equity","income","expense")),("note","Napomena",()))):
            ttk.Label(frame,text=label,style="Field.TLabel").grid(row=row,column=0,sticky="w",padx=(0,8),pady=5);widget=ttk.Combobox(frame,textvariable=self.vars[key],values=choices,state="readonly",style="Modern.TCombobox") if choices else ttk.Entry(frame,textvariable=self.vars[key],style="Modern.TEntry");widget.grid(row=row,column=1,sticky="ew",pady=5)
        ttk.Button(frame,text="Sačuvaj",style="Primary.TButton",command=self.save).grid(row=4,column=0,sticky="w",pady=(12,0));ttk.Button(frame,text="Otkaži",command=self.destroy).grid(row=4,column=1,sticky="e",pady=(12,0))
    def save(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "accountant"}, "upravljanje kontnim planom", parent=self):
            return
        try:self.app.db.save_ledger_account({key:var.get() for key,var in self.vars.items()})
        except (ValueError,sqlite3.Error) as exc:messagebox.showerror("Konto",str(exc),parent=self);return
        if self.on_saved:self.on_saved()
        self.destroy()


class LedgerEntryDialog(tk.Toplevel):
    def __init__(self,parent:tk.Widget,app:MainApp,*,on_saved:Callable[[],None]|None=None)->None:
        super().__init__(parent);self.app,self.on_saved=app,on_saved;self.title("Nova dvostavna stavka");self.configure(background=BG);self.accounts={f"{a['code']} — {a['name']} [{a['id']}]":a['id'] for a in app.db.list_ledger_accounts(active_only=True)};self.vars={key:tk.StringVar(value=value) for key,value in {"entry_date":today_iso(),"reference":"","description":"","currency":app.company.get("default_currency") or DEFAULT_CURRENCY,"amount":"","debit":"","credit":""}.items()};self._build();center_window(self,650,400);self.transient(parent.winfo_toplevel());self.grab_set()
    def _build(self)->None:
        frame=ttk.Frame(self,style="App.TFrame",padding=14);frame.pack(fill="both",expand=True);frame.columnconfigure(1,weight=1)
        fields=(("entry_date","Datum",()),("reference","Referenca",()),("description","Opis",()),("currency","Valuta",SUPPORTED_CURRENCIES),("amount","Iznos",()),("debit","Duguje konto",tuple(self.accounts)),("credit","Potražuje konto",tuple(self.accounts)))
        for row,(key,label,choices) in enumerate(fields):
            ttk.Label(frame,text=label,style="Field.TLabel").grid(row=row,column=0,sticky="w",padx=(0,8),pady=4);widget=ttk.Combobox(frame,textvariable=self.vars[key],values=choices,state="readonly",style="Modern.TCombobox") if choices else ttk.Entry(frame,textvariable=self.vars[key],style="Modern.TEntry");widget.grid(row=row,column=1,sticky="ew",pady=4)
        ttk.Label(frame,text="Nova stavka se uvek čuva kao nacrt. Nakon pregleda je knjiži ovlašćena osoba; proknjižena istorija se koriguje zasebnom stavkom.",style="Help.TLabel",wraplength=580).grid(row=7,column=0,columnspan=2,sticky="w",pady=(6,10))
        ttk.Button(frame,text="Sačuvaj nacrt",style="Primary.TButton",command=self.save).grid(row=8,column=0,sticky="w");ttk.Button(frame,text="Otkaži",command=self.destroy).grid(row=8,column=1,sticky="e")
    def save(self)->None:
        if not self.app.require_team_permission({"owner", "administrator", "accountant"}, "čuvanje dvostavne stavke", parent=self):
            return
        debit_id,credit_id=self.accounts.get(self.vars["debit"].get()),self.accounts.get(self.vars["credit"].get())
        try:self.app.db.save_journal_entry({"entry_date":self.vars["entry_date"].get(),"reference":self.vars["reference"].get(),"description":self.vars["description"].get(),"status":"draft","lines":[{"account_id":debit_id,"debit":self.vars["amount"].get(),"currency":self.vars["currency"].get()},{"account_id":credit_id,"credit":self.vars["amount"].get(),"currency":self.vars["currency"].get()}]})
        except (ValueError,sqlite3.Error) as exc:messagebox.showerror("Dnevnik",str(exc),parent=self);return
        if self.on_saved:self.on_saved()
        self.destroy()


class JournalEntryReviewDialog(tk.Toplevel):
    """Read-only review surface for one journal entry and its control trail."""
    def __init__(self, parent: tk.Widget, app: MainApp, entry_id: int) -> None:
        super().__init__(parent); self.app, self.entry_id = app, int(entry_id)
        self.title("Pregled dvostavne stavke"); self.configure(background=BG)
        self._build(); center_window(self, 980, 610); self.transient(parent.winfo_toplevel())

    def _build(self) -> None:
        entry = self.app.db.get_journal_entry(self.entry_id)
        outer = ttk.Frame(self, style="App.TFrame", padding=14); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Pregled dvostavne stavke", style="Section.TLabel").pack(anchor="w")
        meta = ttk.Frame(outer, style="Panel.TFrame", padding=11); meta.pack(fill="x", pady=(7, 10))
        details = (
            ("Datum", display_date(entry.get("entry_date"))), ("Status", entry.get("status") or "draft"),
            ("Referenca", entry.get("reference") or "—"), ("Opis", entry.get("description") or "—"),
            ("Napomena", entry.get("note") or "—"),
        )
        for index, (label, value) in enumerate(details):
            ttk.Label(meta, text=f"{label}: {value}", style="Help.TLabel", wraplength=850).grid(row=index, column=0, sticky="w", pady=1)
        ttk.Label(outer, text="Redovi knjiženja", style="CardTitle.TLabel").pack(anchor="w")
        line_frame = ttk.Frame(outer, style="Panel.TFrame", padding=8); line_frame.pack(fill="both", expand=True, pady=(4, 10))
        columns = ("account", "type", "debit", "credit", "currency", "source")
        lines = ttk.Treeview(line_frame, columns=columns, show="headings", height=7); setup_treeview_tree(lines)
        for key, label, width in (("account", "Konto", 310), ("type", "Vrsta", 110), ("debit", "Duguje", 125), ("credit", "Potražuje", 125), ("currency", "Valuta", 85), ("source", "Izvor", 170)):
            lines.heading(key, text=label); lines.column(key, width=width, anchor="e" if key in {"debit", "credit"} else "w", stretch=True)
        lines.pack(fill="both", expand=True)
        for index, line in enumerate(self.app.db.list_journal_lines(self.entry_id)):
            currency = line.get("currency") or DEFAULT_CURRENCY
            source = str(line.get("source_type") or "manual")
            if line.get("source_id"):
                source += f" #{line['source_id']}"
            lines.insert("", "end", values=(f"{line.get('account_code')} — {line.get('account_name')}", line.get("account_type"), fmt_money(line.get("debit_amount"), currency), fmt_money(line.get("credit_amount"), currency), currency, source), tags=(tree_row_tag(index),))
        ttk.Label(outer, text="Kontrolni trag", style="CardTitle.TLabel").pack(anchor="w")
        audit_frame = ttk.Frame(outer, style="Panel.TFrame", padding=8); audit_frame.pack(fill="both", expand=True, pady=(4, 10))
        audit = ttk.Treeview(audit_frame, columns=("time", "action", "details"), show="headings", height=5); setup_treeview_tree(audit)
        for key, label, width in (("time", "Vreme", 155), ("action", "Akcija", 190), ("details", "Detalj", 580)):
            audit.heading(key, text=label); audit.column(key, width=width, anchor="w", stretch=True)
        audit.pack(fill="both", expand=True)
        for index, row in enumerate(self.app.db.list_financial_audit("journal_entry", self.entry_id)):
            audit.insert("", "end", values=(row.get("created_at"), row.get("action_code"), row.get("details")), tags=(tree_row_tag(index),))
        ttk.Button(outer, text="Zatvori", command=self.destroy).pack(anchor="e")


class BankingTab(ttk.Frame):
    """Review imported statement lines before they become invoice payments."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master, style="App.TFrame")
        self.app = app
        self.transaction_ids: dict[str, int] = {}
        self.show_closed_var = tk.BooleanVar(value=False)
        self.pending_var = tk.StringVar()
        self.suggested_var = tk.StringVar()
        self.confirmed_var = tk.StringVar()
        self._build()

    def _metric(self, parent: ttk.Frame, title: str, value: tk.StringVar) -> None:
        card = ttk.Frame(parent, style="Panel.TFrame", relief="solid", borderwidth=1, padding=10)
        card.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=value, style="CardValue.TLabel").pack(anchor="w", pady=(3, 0))

    def _build(self) -> None:
        root = ttk.Frame(self, style="App.TFrame")
        root.pack(fill="both", expand=True, padx=12, pady=12)

        header = ttk.Frame(root, style="Panel.TFrame", padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="Bankovni izvodi i uplate", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Uvezite CSV ili XLSX izvod. OpsNest predlaže vezu za priliv ili odliv, a knjiženje nastaje tek nakon vaše potvrde.",
            style="Help.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        toolbar = ttk.Frame(root, style="App.TFrame")
        toolbar.pack(fill="x", pady=10)
        ttk.Button(toolbar, text="Uvezi izvod", style="Primary.TButton", command=self.import_statement).pack(side="left")
        ttk.Button(toolbar, text="Potvrdi izabranu uplatu", command=self.confirm_selected).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Potvrdi sve sigurne", command=self.confirm_confident).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Otvori fakturu", command=self.open_selected_invoice).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Ignoriši stavku", command=self.ignore_selected).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Obriši stavku", command=self.delete_selected).pack(side="left", padx=3)
        ttk.Checkbutton(toolbar, text="Prikaži obrađene", variable=self.show_closed_var, command=self.refresh).pack(side="right")
        ttk.Button(toolbar, text="Osveži", command=self.refresh).pack(side="right", padx=6)

        metrics = ttk.Frame(root, style="App.TFrame")
        metrics.pack(fill="x", pady=(0, 10))
        self._metric(metrics, "Za proveru", self.pending_var)
        self._metric(metrics, "Sa predlogom", self.suggested_var)
        self._metric(metrics, "Potvrđene uplate", self.confirmed_var)

        table_frame = ttk.Frame(root, style="Panel.TFrame")
        table_frame.pack(fill="both", expand=True)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ("date", "direction", "payer", "amount", "currency", "reference", "proposal", "reason", "score", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        setup_treeview_tree(self.tree)
        for key, title, width, anchor, stretch in [
            ("date", "Datum", 100, "w", False),
            ("direction", "Tok", 82, "w", False),
            ("payer", "Uplatilac", 190, "w", True),
            ("amount", "Iznos", 120, "e", False),
            ("currency", "Valuta", 70, "w", False),
            ("reference", "Referenca", 180, "w", True),
            ("proposal", "Predlog fakture", 210, "w", True),
            ("reason", "Osnov predloga", 160, "w", True),
            ("score", "Sigurnost", 85, "e", False),
            ("status", "Status", 105, "w", False),
        ]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch)
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _event: self.confirm_selected())
        self.tree.bind("<Return>", lambda _event: self.confirm_selected())

    def _selected_transaction_id(self) -> int | None:
        selection = self.tree.selection()
        return self.transaction_ids.get(selection[0]) if selection else None

    def import_statement(self) -> None:
        if not self.app.require_plan_feature("bank_matching", parent=self.winfo_toplevel()):
            return
        source = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="Izaberite bankovni izvod",
            filetypes=[("Bankovni izvodi", "*.csv *.xlsx"), ("CSV", "*.csv"), ("Excel", "*.xlsx")],
        )
        if not source:
            return
        try:
            rows = read_bank_statement(source)
            if not rows:
                messagebox.showinfo(
                    "Bankovni izvod",
                    "Na izvodu nisu pronađene stavke sa datumom i iznosom.",
                    parent=self.winfo_toplevel(),
                )
                return
            result = self.app.db.import_bank_transactions(
                rows,
                source_file=Path(source).name,
                source_hash=statement_file_hash(source),
            )
        except Exception as exc:
            messagebox.showerror("Uvoz bankovnog izvoda", f"Izvod nije moguće uvesti:\n{exc}", parent=self.winfo_toplevel())
            return
        self.app.refresh_all()
        ignored_currency_note = ""
        if result.get("ignored_non_eur"):
            ignored_currency_note = (
                f"\nPreskočeno zbog valute koja nije EUR: {result['ignored_non_eur']}"
            )
        messagebox.showinfo(
            "Bankovni izvod",
            f"Uvezeno stavki: {result['inserted']}\n"
            f"Sa predlogom fakture: {result['suggested']}\n"
            f"Već uvezeno iz istog fajla: {result['skipped']}"
            f"{ignored_currency_note}\n\n"
            "Pregledajte predloge i potvrdite samo ispravne uplate.",
            parent=self.winfo_toplevel(),
        )

    def confirm_selected(self) -> None:
        if not self.app.require_plan_feature("bank_matching", parent=self.winfo_toplevel()):
            return
        transaction_id = self._selected_transaction_id()
        if not transaction_id:
            messagebox.showinfo("Banka", "Izaberite bankovnu stavku.", parent=self.winfo_toplevel())
            return
        transaction = self.app.db.get_bank_transaction(transaction_id)
        if transaction.get("status") in {"confirmed", "ignored"}:
            messagebox.showinfo("Banka", "Izabrana stavka je već obrađena.", parent=self.winfo_toplevel())
            return
        if transaction.get("direction") == "outflow":
            BankOutflowDialog(self, self.app, transaction, on_saved=self.app.refresh_all)
        else:
            BankMatchDialog(self, self.app, transaction, on_saved=self.app.refresh_all)

    def confirm_confident(self) -> None:
        if not self.app.require_plan_feature("bank_matching", parent=self.winfo_toplevel()):
            return
        count = sum(
            1
            for row in self.app.db.list_bank_transactions(include_closed=False)
            if row.get("status") == "suggested" and row.get("direction") == "inflow" and int(row.get("match_score") or 0) >= 90
        )
        if not count:
            messagebox.showinfo("Banka", "Nema sigurnih predloga za potvrdu.", parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno(
            "Potvrdi sigurne uplate",
            f"Potvrditi {count} uplata koje imaju broj fakture u referenci?\n\n"
            "Uplate sa manjom sigurnošću ostaće za ručnu proveru.",
            parent=self.winfo_toplevel(),
        ):
            return
        result = self.app.db.confirm_confident_bank_transactions()
        self.app.refresh_all()
        messagebox.showinfo(
            "Banka",
            f"Potvrđene uplate: {result['confirmed']}\nPreskočene stavke: {result['skipped']}",
            parent=self.winfo_toplevel(),
        )

    def open_selected_invoice(self) -> None:
        transaction_id = self._selected_transaction_id()
        if not transaction_id:
            messagebox.showinfo("Banka", "Izaberite bankovnu stavku.", parent=self.winfo_toplevel())
            return
        transaction = self.app.db.get_bank_transaction(transaction_id)
        if transaction.get("direction") == "outflow":
            messagebox.showinfo("Banka", "Odliv se povezuje sa obavezom dobavljača kroz dugme Potvrdi izabranu uplatu.", parent=self.winfo_toplevel())
            return
        invoice_id = int(transaction.get("matched_invoice_id") or transaction.get("suggested_invoice_id") or 0)
        if not invoice_id:
            messagebox.showinfo("Banka", "Za ovu stavku još nema predložene fakture.", parent=self.winfo_toplevel())
            return
        self.app.open_invoice_editor(invoice_id)
        self.app.refresh_all()

    def ignore_selected(self) -> None:
        transaction_id = self._selected_transaction_id()
        if not transaction_id:
            messagebox.showinfo("Banka", "Izaberite bankovnu stavku.", parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno("Ignoriši stavku", "Skloniti ovu stavku iz pregleda za potvrdu uplata?", parent=self.winfo_toplevel()):
            return
        try:
            self.app.db.ignore_bank_transaction(transaction_id)
        except ValueError as exc:
            messagebox.showerror("Banka", str(exc), parent=self.winfo_toplevel())
            return
        self.app.refresh_all()

    def delete_selected(self) -> None:
        transaction_id = self._selected_transaction_id()
        if not transaction_id:
            messagebox.showinfo("Banka", "Izaberite bankovnu stavku.", parent=self.winfo_toplevel())
            return
        transaction = self.app.db.get_bank_transaction(transaction_id)
        if not transaction:
            messagebox.showerror("Banka", "Bankovna stavka više ne postoji.", parent=self.winfo_toplevel())
            return
        direction = "odliv" if transaction.get("direction") == "outflow" else "priliv"
        status = str(transaction.get("status") or "new")
        extra = "\n\nPotvrđeni iznos će prvo biti vraćen na fakturu ili obavezu dobavljača." if status == "confirmed" else ""
        if not messagebox.askyesno(
            "Obriši bankovnu stavku",
            f"Obrisati {direction} iz bankovnog pregleda?{extra}",
            parent=self.winfo_toplevel(),
        ):
            return
        try:
            self.app.db.delete_bank_transaction(transaction_id)
        except ValueError as exc:
            messagebox.showerror("Banka", str(exc), parent=self.winfo_toplevel())
            return
        self.app.refresh_all()

    def refresh(self) -> None:
        rows = self.app.db.list_bank_transactions(include_closed=self.show_closed_var.get())
        summary = self.app.db.bank_transaction_summary()
        self.pending_var.set(f"{int(summary.get('pending_count') or 0)} | {fmt_money(summary.get('pending_amount') or 0)}")
        self.suggested_var.set(str(int(summary.get("suggested_count") or 0)))
        self.confirmed_var.set(str(int(summary.get("confirmed_count") or 0)))
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.transaction_ids.clear()
        status_labels = {
            "new": tr("Bez predloga"),
            "suggested": tr("Predlog"),
            "confirmed": tr("Potvrđena"),
            "ignored": tr("Ignorisana"),
        }
        for index, row in enumerate(rows):
            status = str(row.get("status") or "new")
            is_outflow = row.get("direction") == "outflow"
            if status == "confirmed":
                invoice_number = row.get("matched_invoice_number") or ""
                customer_name = row.get("matched_customer_name") or ""
                if is_outflow:
                    invoice_number = row.get("matched_vendor_bill_number") or ""
                    customer_name = row.get("matched_vendor_name") or ""
                proposal = f"{invoice_number} | {customer_name}".strip(" |")
            else:
                invoice_number = row.get("suggested_invoice_number") or ""
                customer_name = row.get("suggested_customer_name") or ""
                if is_outflow:
                    invoice_number = row.get("suggested_vendor_bill_number") or ""
                    customer_name = row.get("suggested_vendor_name") or ""
                proposal = f"{invoice_number} | {customer_name}".strip(" |")
            item_id = f"bank-{row['id']}"
            self.tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    display_date(row.get("transaction_date")),
                    "Odliv" if is_outflow else "Priliv",
                    row.get("payer_name") or "",
                    fmt_money(row.get("amount") or 0, row.get("currency") or DEFAULT_CURRENCY),
                    row.get("currency") or "",
                    row.get("reference") or row.get("description") or "",
                    proposal,
                    row.get("match_reason") or "",
                    f"{int(row.get('match_score') or 0)}%" if row.get("match_score") else "",
                    status_labels.get(status, status),
                ),
                tags=(tree_row_tag(index),),
            )
            self.transaction_ids[item_id] = int(row["id"])


class BankOutflowDialog(tk.Toplevel):
    """Explicitly settle a supplier liability from a reviewed bank outflow."""
    def __init__(self, master: tk.Widget, app: MainApp, transaction: dict[str, Any], on_saved: Callable[[], None]) -> None:
        super().__init__(master); self.app,self.transaction,self.on_saved=app,transaction,on_saved;self.bill_ids={};self.bill_var=tk.StringVar()
        self.title("Potvrdi odliv sa izvoda");self.configure(background=BG);self._build();self.transient(master.winfo_toplevel());self.grab_set();center_window(self,760,410)
    def _build(self) -> None:
        outer=ttk.Frame(self,style="App.TFrame",padding=16);outer.pack(fill="both",expand=True);outer.columnconfigure(1,weight=1)
        ttk.Label(outer,text="Potvrdi plaćanje dobavljaču",style="Section.TLabel").grid(row=0,column=0,columnspan=2,sticky="w")
        for row,(label,value) in enumerate((("Datum",display_date(self.transaction.get("transaction_date"))),("Primalac",self.transaction.get("payer_name") or "-"),("Iznos",fmt_money(self.transaction.get("amount"),self.transaction.get("currency") or DEFAULT_CURRENCY)),("Referenca",self.transaction.get("reference") or self.transaction.get("description") or "-")),start=1):
            ttk.Label(outer,text=label,style="Field.TLabel").grid(row=row,column=0,sticky="w",padx=(0,12),pady=4);ttk.Label(outer,text=value,style="CardTitle.TLabel",wraplength=520).grid(row=row,column=1,sticky="w",pady=4)
        currency=str(self.transaction.get("currency") or "").upper();choices=[]
        for bill in self.app.db.list_vendor_bills(include_paid=False):
            if str(bill.get("currency") or "").upper()!=currency:continue
            if str(bill.get("approval_status") or "approved") != "approved":
                continue
            label=f"{bill.get('bill_number') or '#'+str(bill['id'])} | {bill.get('vendor_name')} | za plaćanje {fmt_money(bill.get('balance_amount'),currency)}"
            choices.append(label);self.bill_ids[label]=int(bill["id"])
            if int(self.transaction.get("suggested_vendor_bill_id") or 0)==int(bill["id"]):self.bill_var.set(label)
        ttk.Label(outer,text="Obaveza",style="Field.TLabel").grid(row=5,column=0,sticky="w",padx=(0,12),pady=(14,4));combo=ttk.Combobox(outer,textvariable=self.bill_var,values=choices,state="readonly",style="Modern.TCombobox");combo.grid(row=5,column=1,sticky="ew",pady=(14,4))
        if choices and not self.bill_var.get():combo.current(0)
        if not choices:ttk.Label(outer,text="Nema otvorenih obaveza u istoj valuti. Najpre je unesite u Finansije.",style="Help.TLabel").grid(row=6,column=1,sticky="w")
        buttons=ttk.Frame(outer,style="App.TFrame");buttons.grid(row=7,column=0,columnspan=2,sticky="ew",pady=(18,0));ttk.Button(buttons,text="Potvrdi plaćanje",style="Primary.TButton",command=self.save).pack(side="left");ttk.Button(buttons,text="Otkaži",command=self.destroy).pack(side="right")
    def save(self) -> None:
        bill_id=self.bill_ids.get(self.bill_var.get())
        if not bill_id:messagebox.showerror("Banka","Izaberite obavezu dobavljača.",parent=self);return
        try:self.app.db.confirm_bank_outflow(int(self.transaction["id"]),bill_id,confirmed_by_name=self.app.active_team_member_name())
        except ValueError as exc:messagebox.showerror("Banka",str(exc),parent=self);return
        self.on_saved();self.destroy()


class BankMatchDialog(tk.Toplevel):
    def __init__(self, master: tk.Widget, app: MainApp, transaction: dict[str, Any], on_saved: Callable[[], None]) -> None:
        super().__init__(master)
        self.app = app
        self.transaction = transaction
        self.on_saved = on_saved
        self.invoice_ids: dict[str, int] = {}
        self.invoice_var = tk.StringVar()
        self.title("Potvrdi bankovnu uplatu")
        self.configure(background=BG)
        self._build()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        center_window(self, 760, 370)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="Potvrdi uplatu sa bankovnog izvoda", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        values = [
            ("Datum", display_date(self.transaction.get("transaction_date"))),
            ("Uplatilac", self.transaction.get("payer_name") or "-"),
            ("Iznos", fmt_money(self.transaction.get("amount") or 0, self.transaction.get("currency") or DEFAULT_CURRENCY)),
            ("Referenca", self.transaction.get("reference") or self.transaction.get("description") or "-"),
        ]
        for row, (label, value) in enumerate(values, start=1):
            ttk.Label(outer, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Label(outer, text=value, style="CardTitle.TLabel", wraplength=520).grid(row=row, column=1, sticky="w", pady=4)

        ttk.Label(outer, text="Faktura", style="Field.TLabel").grid(row=5, column=0, sticky="w", padx=(0, 12), pady=(14, 4))
        invoices = self.app.db.list_invoices(open_only=True)
        transaction_currency = str(self.transaction.get("currency") or "").upper()
        choices: list[str] = []
        for invoice in invoices:
            if transaction_currency and str(invoice.get("currency") or "").upper() != transaction_currency:
                continue
            label = (
                f"{invoice.get('invoice_number')} | {invoice.get('customer_name')} | "
                f"{invoice.get('project_name')} | dug {fmt_money(invoice.get('balance_total') or 0, invoice.get('currency') or DEFAULT_CURRENCY)}"
            )
            choices.append(label)
            self.invoice_ids[label] = int(invoice["id"])
            if int(self.transaction.get("suggested_invoice_id") or 0) == int(invoice["id"]):
                self.invoice_var.set(label)
        combo = ttk.Combobox(outer, textvariable=self.invoice_var, values=choices, state="readonly", style="Modern.TCombobox")
        combo.grid(row=5, column=1, sticky="ew", pady=(14, 4))
        if not choices:
            ttk.Label(
                outer,
                text="Nema otvorenih faktura u istoj valuti za povezivanje.",
                style="Help.TLabel",
            ).grid(row=6, column=1, sticky="w")
        elif not self.invoice_var.get():
            combo.current(0)

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ttk.Button(buttons, text="Potvrdi uplatu", style="Primary.TButton", command=self.save).pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")

    def save(self) -> None:
        invoice_id = self.invoice_ids.get(self.invoice_var.get())
        if not invoice_id:
            messagebox.showerror("Banka", "Izaberite fakturu za uplatu.", parent=self)
            return
        try:
            self.app.db.confirm_bank_transaction(int(self.transaction["id"]), invoice_id)
        except ValueError as exc:
            messagebox.showerror("Banka", str(exc), parent=self)
            return
        self.on_saved()
        self.destroy()


class CustomerInvoicesDialog(tk.Toplevel):
    """Dashboard drill-down that keeps one customer's invoices together across projects."""

    def __init__(self, parent: tk.Widget, app: MainApp, customer_name: str) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.customer_name = customer_name
        self._invoice_ids: dict[str, int] = {}
        self.total_var = tk.StringVar()
        self.title(f"Fakture kupca: {customer_name}")
        self.configure(background=BG)
        self.resizable(True, True)
        self._build()
        self.refresh()
        maximize_large_window(self, minimum_width=920, minimum_height=560)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(2, weight=1)
        outer.columnconfigure(0, weight=1)

        header = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Fakture kupca", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=self.customer_name, style="CardValue.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(header, textvariable=self.total_var, style="Help.TLabel").grid(row=0, column=1, rowspan=2, sticky="e")

        toolbar = ttk.Frame(outer, style="App.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=10)
        ttk.Button(toolbar, text="Otvori fakturu", style="Primary.TButton", command=self.open_selected_invoice).pack(side="left")
        ttk.Button(toolbar, text="PDF / štampa", command=self.open_selected_invoice_pdf).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Osveži", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Zatvori", command=self.destroy).pack(side="right")

        table_frame = ttk.Frame(outer, style="Panel.TFrame")
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ("number", "project", "issue", "due", "gross", "paid", "balance", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        setup_treeview_tree(self.tree)
        for key, label, width, anchor in [
            ("number", "Broj fakture", 130, "w"),
            ("project", "Projekat", 220, "w"),
            ("issue", "Datum", 110, "w"),
            ("due", "Rok", 110, "w"),
            ("gross", "Ukupno", 125, "e"),
            ("paid", "Plaćeno", 125, "e"),
            ("balance", "Dug", 125, "e"),
            ("status", "Status", 135, "w"),
        ]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor, stretch=key == "project")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _event: self.open_selected_invoice())
        self.tree.bind("<Return>", lambda _event: self.open_selected_invoice())

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._invoice_ids.clear()
        rows = self.db.list_customer_invoices(self.customer_name)
        total_gross = sum((row.get("gross_total") or 0) for row in rows)
        total_balance = sum((row.get("balance_total") or 0) for row in rows)
        self.total_var.set(f"Faktura: {len(rows)} | Ukupno: {fmt_money(total_gross)} | Dug: {fmt_money(total_balance)}")
        for idx, row in enumerate(rows):
            item_id = f"invoice-{row['id']}"
            self.tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    row.get("invoice_number") or "",
                    row.get("project_name") or "",
                    display_date(row.get("issue_date")),
                    display_date(row.get("due_date")),
                    fmt_money(row.get("gross_total") or 0),
                    fmt_money(row.get("paid_total") or 0),
                    fmt_money(row.get("balance_total") or 0),
                    localized_status_label(row.get("status_code") or "draft"),
                ),
                tags=(tree_row_tag(idx),),
            )
            self._invoice_ids[item_id] = int(row["id"])

    def open_selected_invoice(self) -> None:
        selection = self.tree.selection()
        invoice_id = self._invoice_ids.get(selection[0]) if selection else None
        if not invoice_id:
            messagebox.showinfo("Fakture kupca", "Izaberite fakturu iz liste.", parent=self)
            return
        self.app.open_invoice_editor(invoice_id)
        self.refresh()

    def open_selected_invoice_pdf(self) -> None:
        selection = self.tree.selection()
        invoice_id = self._invoice_ids.get(selection[0]) if selection else None
        if not invoice_id:
            messagebox.showinfo("Fakture kupca", "Izaberite fakturu iz liste.", parent=self)
            return
        self.app.open_or_generate_invoice_output(invoice_id, "pdf")


class CompanyTab(ttk.Frame):
    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master, style="App.TFrame")
        self.app = app
        self.vars = {
            "name": tk.StringVar(),
            "eik": tk.StringVar(),
            "vat_number": tk.StringVar(),
            "address": tk.StringVar(),
            "phone": tk.StringVar(),
            "email": tk.StringVar(),
            "bank_name": tk.StringVar(),
            "iban": tk.StringVar(),
            "bic": tk.StringVar(),
            "director_name": tk.StringVar(),
            "logo_path": tk.StringVar(),
            "business_profile": tk.StringVar(value=business_profile_label("general")),
            "country_code": tk.StringVar(value=country_option_label("OTHER")),
            "default_currency": tk.StringVar(value=DEFAULT_CURRENCY),
            "default_vat_rate": tk.StringVar(value="0.20"),
            "vat_regime": tk.StringVar(value=vat_regime_label("standard")),
            "einvoice_route": tk.StringVar(value=einvoice_route_label("automatic")),
            "payment_term_days": tk.StringVar(value=str(DEFAULT_PAYMENT_TERM_DAYS)),
            "exchange_rate": tk.StringVar(value=f"{DEFAULT_EXCHANGE_RATE}"),
            "issue_place": tk.StringVar(),
            "payment_method": tk.StringVar(value=payment_method_default()),
            "smtp_host": tk.StringVar(),
            "smtp_port": tk.StringVar(value=str(DEFAULT_SMTP_PORT)),
            "smtp_security": tk.StringVar(value="tls"),
            "smtp_username": tk.StringVar(),
            "smtp_password": tk.StringVar(),
            "smtp_from_name": tk.StringVar(),
            "smtp_from_email": tk.StringVar(),
            "smtp_reply_to": tk.StringVar(),
            "auto_payment_reminders": tk.BooleanVar(value=False),
            "payment_reminder_interval_days": tk.StringVar(value="7"),
            "vendor_bill_owner_approval_threshold": tk.StringVar(value="0"),
            "ui_language": tk.StringVar(value=language_label("sr")),
        }
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(outer, text="Podaci firme", padding=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=8)
        left.columnconfigure(1, weight=1)
        row = 0
        for key, label in [
            ("name", "Naziv"),
            ("eik", "EIK / BULSTAT"),
            ("vat_number", "PDV broj"),
            ("address", "Adresa"),
            ("phone", "Telefon"),
            ("email", "E-mail"),
            ("bank_name", "Banka"),
            ("iban", "IBAN"),
            ("bic", "BIC / SWIFT"),
            ("director_name", "Direktor"),
            ("logo_path", "Logo putanja"),
        ]:
            add_field(left, row, 0, label, self.vars[key], width=34)
            if key == "logo_path":
                ttk.Button(left, text="Izaberi", command=self.browse_logo).grid(row=row, column=2, sticky="w", padx=4)
            row += 1
        ttk.Button(left, text="Registracija / profil firme", command=self.app.open_company_registration).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

        right = ttk.LabelFrame(outer, text="Podešavanja fakture", padding=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=8)
        right.columnconfigure(1, weight=1)
        add_combo(right, 0, 0, "Delatnost", self.vars["business_profile"], list(BUSINESS_PROFILE_LABELS.values()), width=36)
        self.country_combo = add_combo(right, 1, 0, "Država registracije", self.vars["country_code"], country_option_values())
        self.country_combo.bind("<<ComboboxSelected>>", self._apply_country_vat_default)
        ttk.Label(right, text="Država predlaže valutu i standardnu PDV stopu. Potvrdite režim sa svojim knjigovođom.", style="Help.TLabel", wraplength=300).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 5))
        add_combo(right, 3, 0, "Podrazumevana valuta", self.vars["default_currency"], list(SUPPORTED_CURRENCIES), width=12)
        self.vat_regime_combo = add_combo(right, 4, 0, "PDV režim", self.vars["vat_regime"], list(VAT_REGIME_LABELS.values()), width=36)
        self.vat_regime_combo.bind("<<ComboboxSelected>>", self._apply_vat_regime)
        add_combo(right, 5, 0, "E-faktura tok", self.vars["einvoice_route"], list(EINVOICE_ROUTE_LABELS.values()), width=36)
        add_field(right, 6, 0, "PDV stopa", self.vars["default_vat_rate"], width=12)
        add_field(right, 7, 0, "Rok plaćanja (dani)", self.vars["payment_term_days"], width=12)
        add_combo(right, 8, 0, "Način plaćanja", self.vars["payment_method"], list(PAYMENT_METHOD_OPTIONS))
        add_field(right, 9, 0, "Mesto izdavanja", self.vars["issue_place"], width=20)
        add_combo(right, 10, 0, "Jezik programa", self.vars["ui_language"], list(UI_LANGUAGE_LABELS.values()), width=18)
        add_field(right, 11, 0, "Limit za odobrenje vlasnika", self.vars["vendor_bill_owner_approval_threshold"], width=14)
        ttk.Label(right, text="0 = bez limita. Limit važi samo u osnovnoj valuti; strane valute idu vlasniku.", style="Help.TLabel", wraplength=300).grid(row=12, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Button(right, text="Sačuvaj", style="Primary.TButton", command=self.save).grid(row=13, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Button(right, text="Učitaj iz template-a", command=self.load_template_defaults).grid(row=13, column=1, sticky="e", pady=(12, 0))

        mail = ttk.LabelFrame(outer, text="Slanje e-mailom (SMTP)", padding=12)
        mail.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        for idx in range(4):
            mail.columnconfigure(idx * 2 + 1, weight=1)
        add_field(mail, 0, 0, "SMTP server", self.vars["smtp_host"], width=24)
        add_field(mail, 0, 2, "Port", self.vars["smtp_port"], width=10)
        add_combo(mail, 1, 0, "Bezbednost", self.vars["smtp_security"], SMTP_SECURITY_OPTIONS, width=12)
        add_field(mail, 1, 2, "Korisnik", self.vars["smtp_username"], width=24)
        add_field(mail, 2, 0, "Lozinka", self.vars["smtp_password"], width=24, show="*")
        add_field(mail, 2, 2, "Pošiljalac ime", self.vars["smtp_from_name"], width=24)
        add_field(mail, 3, 0, "Pošiljalac e-mail", self.vars["smtp_from_email"], width=24)
        add_field(mail, 3, 2, "Reply-To", self.vars["smtp_reply_to"], width=24)
        reminders = ttk.Frame(mail, style="App.TFrame")
        reminders.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        reminders.columnconfigure(2, weight=1)
        ttk.Checkbutton(
            reminders,
            text="Uključi automatske podsetnike",
            variable=self.vars["auto_payment_reminders"],
        ).grid(row=0, column=0, sticky="w")
        add_field(reminders, 0, 1, "Razmak podsetnika (dani)", self.vars["payment_reminder_interval_days"], width=7)
        ttk.Label(
            reminders,
            text="Šalje se samo tekst podsetnika kupcu kada faktura dospe, a zatim najviše jednom u izabranom broju dana. Fakture i prilozi se nikada ne šalju automatski.",
            style="Help.TLabel",
            wraplength=620,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Button(mail, text="Test SMTP", style="Primary.TButton", command=self.test_email).grid(row=5, column=0, columnspan=4, sticky="w", pady=(12, 0))

    def browse_logo(self) -> None:
        path = filedialog.askopenfilename(title="Izaberi logo", filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if path:
            self.vars["logo_path"].set(path)

    def load_template_defaults(self) -> None:
        self.vars["business_profile"].set(business_profile_label("general"))
        self.vars["country_code"].set(country_option_label("OTHER"))
        self.vars["default_currency"].set(DEFAULT_CURRENCY)
        self.vars["default_vat_rate"].set("0.20")
        self.vars["vat_regime"].set(vat_regime_label("standard"))
        self.vars["einvoice_route"].set(einvoice_route_label("automatic"))
        self.vars["exchange_rate"].set(f"{DEFAULT_EXCHANGE_RATE}")
        self.vars["payment_term_days"].set(str(DEFAULT_PAYMENT_TERM_DAYS))
        self.vars["payment_method"].set(payment_method_default())
        self.vars["vendor_bill_owner_approval_threshold"].set("0")
        self.vars["issue_place"].set("Sofija")
        self.vars["smtp_security"].set("tls")
        self.vars["smtp_port"].set(str(DEFAULT_SMTP_PORT))
        self.vars["smtp_from_name"].set(self.vars["name"].get().strip())
        self.vars["smtp_reply_to"].set("")

    def _apply_country_vat_default(self, _event: tk.Event | None = None) -> None:
        country_code = country_code_from_option(self.vars["country_code"].get())
        self.vars["default_currency"].set(default_currency_for_country(country_code))
        if vat_regime_code_from_label(self.vars["vat_regime"].get()) == "standard":
            self.vars["default_vat_rate"].set(f"{default_vat_rate_for_country(country_code):.2f}")

    def _apply_vat_regime(self, _event: tk.Event | None = None) -> None:
        if vat_regime_code_from_label(self.vars["vat_regime"].get()) == "standard":
            self._apply_country_vat_default()
        else:
            self.vars["default_vat_rate"].set("0.00")

    def refresh(self) -> None:
        company = self.app.db.get_company()
        for key in self.vars:
            value = company.get(key, "")
            if value is None:
                value = ""
            if key == "country_code":
                value = country_option_label(value)
            if key == "business_profile":
                value = business_profile_label(value)
            if key == "vat_regime":
                value = vat_regime_label(value)
            if key == "einvoice_route":
                value = einvoice_route_label(value)
            if key in {"default_vat_rate", "exchange_rate"} and value != "":
                value = f"{float(value):.2f}" if key == "default_vat_rate" else str(value)
            if key in {"payment_term_days", "smtp_port", "payment_reminder_interval_days"} and value != "":
                value = str(value)
            if key == "vendor_bill_owner_approval_threshold":
                value = f"{float(value or 0):.2f}"
            if key == "smtp_security" and value:
                value = str(value).lower()
            if key == "ui_language":
                value = language_label(value)
            if key == "auto_payment_reminders":
                self.vars[key].set(bool(int(value or 0)))
                continue
            self.vars[key].set(str(value))

    def save(self) -> None:
        try:
            payload = {
                "name": self.vars["name"].get().strip(),
                "eik": self.vars["eik"].get().strip(),
                "vat_number": self.vars["vat_number"].get().strip(),
                "address": self.vars["address"].get().strip(),
                "phone": self.vars["phone"].get().strip(),
                "email": self.vars["email"].get().strip(),
                "bank_name": self.vars["bank_name"].get().strip(),
                "iban": self.vars["iban"].get().strip(),
                "bic": self.vars["bic"].get().strip(),
                "director_name": self.vars["director_name"].get().strip(),
                "logo_path": self.vars["logo_path"].get().strip(),
                "business_profile": business_profile_code_from_label(self.vars["business_profile"].get()),
                "country_code": country_code_from_option(self.vars["country_code"].get()),
                "default_currency": self.vars["default_currency"].get().strip() or DEFAULT_CURRENCY,
                "default_vat_rate": float(self.vars["default_vat_rate"].get() or 0.2),
                "vat_regime": vat_regime_code_from_label(self.vars["vat_regime"].get()),
                "einvoice_route": einvoice_route_code_from_label(self.vars["einvoice_route"].get()),
                "payment_term_days": int(self.vars["payment_term_days"].get() or DEFAULT_PAYMENT_TERM_DAYS),
                "exchange_rate": float(self.vars["exchange_rate"].get() or DEFAULT_EXCHANGE_RATE),
                "issue_place": self.vars["issue_place"].get().strip(),
                "payment_method": self.vars["payment_method"].get().strip() or payment_method_default(),
                "smtp_host": self.vars["smtp_host"].get().strip(),
                "smtp_port": int(self.vars["smtp_port"].get() or DEFAULT_SMTP_PORT),
                "smtp_security": self.vars["smtp_security"].get().strip().lower() or "tls",
                "smtp_username": self.vars["smtp_username"].get().strip(),
                "smtp_password": self.vars["smtp_password"].get(),
                "smtp_from_name": self.vars["smtp_from_name"].get().strip() or self.vars["name"].get().strip(),
                "smtp_from_email": self.vars["smtp_from_email"].get().strip(),
                "smtp_reply_to": self.vars["smtp_reply_to"].get().strip(),
                "auto_payment_reminders": int(bool(self.vars["auto_payment_reminders"].get())),
                "payment_reminder_interval_days": int(self.vars["payment_reminder_interval_days"].get() or 7),
                "vendor_bill_owner_approval_threshold": float(self.vars["vendor_bill_owner_approval_threshold"].get() or 0),
                "ui_language": language_code_from_label(self.vars["ui_language"].get()),
                "onboarding_completed": 1,
            }
        except ValueError as exc:
            messagebox.showerror("Greška", f"Nije moguće sačuvati firmu: {exc}")
            return
        self.app.db.save_company(payload)
        self.app.company = self.app.db.get_company()
        self.app.apply_language(payload["ui_language"], persist=False)
        self.app._automatic_reminder_check_started = False
        self.app.after(250, self.app.send_due_payment_reminders_silently)
        messagebox.showinfo("Sačuvano", "Podaci firme su sačuvani.")

    def test_email(self) -> None:
        SMTPTestDialog(self, self.app)


class SubscriptionPlanDialog(tk.Toplevel):
    """Pick a fixed monthly plan before opening the secure PayPal checkout."""

    PLANS = (
        ("starter", "Starter", "9,90 EUR / mesečno"),
        ("business", "Business", "19,90 EUR / mesečno"),
        ("pro", "Pro", "29,90 EUR / mesečno"),
    )

    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master)
        self.selected_plan = ""
        self.title("Izbor OpsNest paketa")
        self.configure(background=BG)
        self.resizable(False, False)

        outer = ttk.Frame(self, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Izaberite OpsNest paket", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Plaćanje se bezbedno završava preko PayPal-a. Pretplatu možete otkazati iz PayPal naloga.",
            style="Help.TLabel",
            wraplength=560,
        ).pack(anchor="w", pady=(3, 14))

        plans = ttk.Frame(outer, style="App.TFrame")
        plans.pack(fill="x")
        for index, (code, name, price) in enumerate(self.PLANS):
            card = ttk.LabelFrame(plans, text=name, padding=12)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0))
            plans.columnconfigure(index, weight=1)
            ttk.Label(card, text=price, style="Value.TLabel").pack(anchor="w")
            ttk.Label(card, text="mesečna pretplata", style="Help.TLabel").pack(anchor="w", pady=(2, 10))
            ttk.Button(card, text=f"Izaberi {name}", style="Primary.TButton", command=lambda value=code: self.choose(value)).pack(anchor="w")

        footer = ttk.Frame(outer, style="App.TFrame")
        footer.pack(fill="x", pady=(14, 0))
        ttk.Button(footer, text="Otkaži", command=self.destroy).pack(side="right")

        self.transient(master.winfo_toplevel())
        self.grab_set()
        center_window(self, 670, 300)
        localize_widget_tree(self, active_ui_language())
        self.bind("<Escape>", lambda _event: self.destroy())

    def choose(self, plan_code: str) -> None:
        self.selected_plan = plan_code
        self.destroy()


class LegacyTeamMembersDialog(tk.Toplevel):
    """Keep the package seats visible before shared cloud accounts are enabled."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.selected_id: int | None = None
        self.members_by_id: dict[int, dict[str, Any]] = {}
        self.name_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.role_var = tk.StringVar(value=tr("Član"))
        self.status_var = tk.StringVar(value=tr("Pozvan"))
        self.seats_var = tk.StringVar()
        self.help_var = tk.StringVar()
        self.title("Korisnici firme")
        self.configure(background=BG)
        self.resizable(True, True)
        self._build()
        self.refresh()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        center_window(self, 920, 560)
        self.minsize(760, 480)
        localize_widget_tree(self, active_ui_language())
        self.bind("<Escape>", lambda _event: self.destroy())

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(2, weight=1)

        ttk.Label(outer, text="Korisnici firme", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, textvariable=self.seats_var, style="Value.TLabel").grid(row=0, column=1, sticky="e")
        ttk.Label(
            outer,
            text="Spisak tima trenutno čuva mesta, uloge i e-mail adrese. Posebne cloud prijave i zajednička sinhronizacija dolaze u sledećoj fazi.",
            style="Help.TLabel",
            wraplength=850,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 12))

        list_frame = ttk.LabelFrame(outer, text="Korisnici firme", padding=8)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        columns = ("name", "email", "role", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        setup_treeview_tree(self.tree)
        for key, label, width in (
            ("name", "Ime korisnika", 170),
            ("email", "E-mail", 220),
            ("role", "Uloga", 110),
            ("status", "Status", 100),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        editor = ttk.LabelFrame(outer, text="Dodaj korisnika", padding=10)
        editor.grid(row=2, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        add_field(editor, 0, 0, "Ime korisnika", self.name_var, width=28)
        add_field(editor, 1, 0, "E-mail", self.email_var, width=28)
        self.role_combo = add_combo(editor, 2, 0, "Uloga", self.role_var, [tr("Član"), tr("Knjigovođa")], width=20)
        self.status_combo = add_combo(editor, 3, 0, "Status", self.status_var, [tr("Pozvan"), tr("Aktivan")], width=20)
        ttk.Label(editor, textvariable=self.help_var, style="Help.TLabel", wraplength=300).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Novi korisnik", command=self.new_member).pack(side="left")
        ttk.Button(buttons, text="Sačuvaj korisnika", style="Primary.TButton", command=self.save_member).pack(side="left", padx=6)
        ttk.Button(buttons, text="Ukloni korisnika", command=self.archive_member).pack(side="left")
        ttk.Button(buttons, text="Osveži", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(buttons, text="Paketi i plaćanje", command=self.app.open_plan_and_billing).pack(side="left")
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="right")

    @staticmethod
    def _stored_role(value: str) -> str:
        return "accountant" if canonical_ui_text(value, active_ui_language()) == "Knjigovođa" else "member"

    @staticmethod
    def _stored_status(value: str) -> str:
        return "active" if canonical_ui_text(value, active_ui_language()) == "Aktivan" else "invited"

    def _role_label(self, value: str) -> str:
        return tr("Knjigovođa") if value == "accountant" else tr("Član")

    def _status_label(self, value: str) -> str:
        return tr("Aktivan") if value == "active" else tr("Pozvan")

    def refresh(self) -> None:
        usage = self.app.db.plan_usage()
        details = usage["details"]
        seats = usage["limits"].get("seats")
        seat_limit = tr("Neograničeno") if seats is None else str(seats)
        self.seats_var.set(f"{tr('Plan i mesta')}: {details['name']} - {usage['team_seats_used']} / {seat_limit}")
        features = set(details.get("features") or set())
        self.help_var.set(
            tr("Za dodavanje korisnika potreban je Business ili Pro paket.")
            if "team_users" not in features
            else ""
        )
        self.role_combo["values"] = [tr("Član"), tr("Knjigovođa")]
        self.status_combo["values"] = [tr("Pozvan"), tr("Aktivan")]
        self.members_by_id = {int(row["id"]): row for row in self.app.db.team_members()}
        for item in self.tree.get_children():
            self.tree.delete(item)
        company = self.app.db.get_company()
        owner_name = str(company.get("director_name") or company.get("name") or tr("Vlasnik (lokalni profil)"))
        owner_email = str(company.get("login_email") or company.get("email") or "-")
        self.tree.insert("", "end", iid="owner", values=(owner_name, owner_email, tr("Vlasnik (lokalni profil)"), tr("Aktivan")))
        for index, member in enumerate(self.members_by_id.values(), start=1):
            self.tree.insert(
                "",
                "end",
                iid=str(member["id"]),
                values=(
                    member.get("display_name") or "",
                    member.get("email") or "",
                    self._role_label(str(member.get("role") or "member")),
                    self._status_label(str(member.get("status") or "invited")),
                ),
                tags=(tree_row_tag(index),),
            )

    def new_member(self) -> None:
        self.selected_id = None
        self.name_var.set("")
        self.email_var.set("")
        self.role_var.set(tr("Član"))
        self.status_var.set(tr("Pozvan"))
        self.tree.selection_remove(self.tree.selection())

    def _on_select(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        item_id = selected[0]
        if item_id == "owner":
            self.selected_id = None
            self.help_var.set(tr("Vlasnik (lokalni profil)"))
            return
        member = self.members_by_id.get(int(item_id))
        if not member:
            return
        self.selected_id = int(item_id)
        self.name_var.set(str(member.get("display_name") or ""))
        self.email_var.set(str(member.get("email") or ""))
        self.role_var.set(self._role_label(str(member.get("role") or "member")))
        self.status_var.set(self._status_label(str(member.get("status") or "invited")))
        self.help_var.set("")

    def save_member(self) -> None:
        try:
            self.app.db.save_team_member({
                "id": self.selected_id,
                "display_name": self.name_var.get(),
                "email": self.email_var.get(),
                "role": self._stored_role(self.role_var.get()),
                "status": self._stored_status(self.status_var.get()),
            })
        except PlanLimitError as exc:
            if messagebox.askyesno(tr("OpsNest paket"), f"{exc}\n\n{tr('Otvoriti pakete i plaćanje?')}", parent=self):
                self.app.open_plan_and_billing()
            return
        except ValueError as exc:
            messagebox.showerror("OpsNest", str(exc), parent=self)
            return
        self.new_member()
        self.refresh()

    def archive_member(self) -> None:
        if not self.selected_id:
            return
        if not messagebox.askyesno("OpsNest", tr("Ukloni korisnika") + "?", parent=self):
            return
        self.app.db.archive_team_member(self.selected_id)
        self.new_member()
        self.refresh()


class TeamMembersDialog(tk.Toplevel):
    """Central team administration backed by revocable cloud accounts."""

    ROLE_OPTIONS = (
        ("owner", "Vlasnik / administrator"),
        ("administrator", "Administrator"),
        ("project_manager", "Menadžer projekta"),
        ("accountant", "Knjigovođa"),
        ("operator", "Operater"),
    )
    ROLE_LABELS = dict(ROLE_OPTIONS)
    STATUS_LABELS = {"active": "Aktivan", "invited": "Pozvan", "revoked": "Ukinut"}

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.members: dict[str, dict[str, Any]] = {}
        self.selected_member_id = ""
        self.status_var = tk.StringVar()
        self.seats_var = tk.StringVar()
        self.workspace_var = tk.StringVar(value="ID radnog prostora: -")
        self.name_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.role_var = tk.StringVar(value=self.ROLE_OPTIONS[3][1])
        self.title("OpsNest tim")
        self.configure(background=BG)
        self._build()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        maximize_large_window(self, minimum_width=1040, minimum_height=620)
        self.refresh()
        self.bind("<Escape>", lambda _event: self.destroy())

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(2, weight=1)

        ttk.Label(outer, text="Centralni tim i sinhronizacija", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, textvariable=self.seats_var, style="Value.TLabel").grid(row=0, column=1, sticky="e")
        workspace_line = ttk.Frame(outer, style="App.TFrame")
        workspace_line.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Label(workspace_line, textvariable=self.workspace_var, style="Help.TLabel").pack(side="left")
        ttk.Button(workspace_line, text="Kopiraj ID", command=self.copy_workspace_id).pack(side="left", padx=(8, 0))
        ttk.Label(
            outer,
            text=(
                "Svaki član se prijavljuje svojim e-mailom i lozinkom. Vlasnik i administrator šalju pozive, "
                "mogu odmah ukinuti pristup i vide ko radi u zajedničkom radnom prostoru. "
                "Preuzimanje i slanje koriste proverenu verziju baze kako se izmene ne bi pregazile."
            ),
            style="Help.TLabel",
            wraplength=950,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 12))

        roster = ttk.LabelFrame(outer, text="Članovi tima", padding=8)
        roster.grid(row=3, column=0, sticky="nsew", padx=(0, 10))
        roster.rowconfigure(0, weight=1)
        roster.columnconfigure(0, weight=1)
        columns = ("name", "email", "role", "status", "last_login")
        self.tree = ttk.Treeview(roster, columns=columns, show="headings", selectmode="browse")
        setup_treeview_tree(self.tree)
        for key, label, width in (
            ("name", "Ime", 150),
            ("email", "E-mail", 225),
            ("role", "Uloga", 165),
            ("status", "Status", 95),
            ("last_login", "Poslednja prijava", 150),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(roster, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        editor = ttk.LabelFrame(outer, text="Pozovi člana", padding=10)
        editor.grid(row=3, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        add_field(editor, 0, 0, "Ime i prezime", self.name_var, width=30)
        add_field(editor, 1, 0, "Poslovni e-mail", self.email_var, width=30)
        self.role_combo = add_combo(
            editor,
            2,
            0,
            "Uloga",
            self.role_var,
            [label for code, label in self.ROLE_OPTIONS if code != "owner"],
            width=26,
        )
        ttk.Label(
            editor,
            text=(
                "Menadžer projekta upravlja projektima i fakturama. Knjigovođa radi račune, PDV i izvoz. "
                "Operater unosi troškove, dokumente i uplate bez brisanja i licence."
            ),
            style="Help.TLabel",
            wraplength=315,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Label(editor, textvariable=self.status_var, style="Help.TLabel", wraplength=315).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Uključi centralni nalog", command=self.setup_owner).pack(side="left")
        ttk.Button(buttons, text="Pošalji poziv", style="Primary.TButton", command=self.invite).pack(side="left", padx=6)
        ttk.Button(buttons, text="Ukloni pristup", command=self.revoke_selected).pack(side="left")
        ttk.Button(buttons, text="Preuzmi podatke", command=self.download_data).pack(side="left", padx=6)
        ttk.Button(buttons, text="Pošalji izmene", command=self.upload_data).pack(side="left")
        ttk.Button(buttons, text="Osveži", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(buttons, text="Paketi i plaćanje", command=self.app.open_plan_and_billing).pack(side="left")
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="right")

    @classmethod
    def _role_code(cls, label: str) -> str:
        return next((code for code, text in cls.ROLE_OPTIONS if text == label), "operator")

    def _connection(self) -> tuple[dict[str, str], str] | None:
        ready = self.app.team_connection_ready()
        if not ready:
            self.status_var.set("Prvo registrujte firmu i uključite centralni nalog.")
        return ready

    def copy_workspace_id(self) -> None:
        workspace_id = str(self.app.db.get_subscription().get("workspace_id") or "").strip()
        if not workspace_id:
            self.status_var.set("ID radnog prostora će biti dostupan posle registracije firme.")
            return
        self.clipboard_clear()
        self.clipboard_append(workspace_id)
        self.status_var.set("ID radnog prostora je kopiran. Pošaljite ga članu tima samo preko pouzdanog kanala.")

    def _is_team_admin(self) -> bool:
        role = self.app.db.cloud_connection().get("member_role", "")
        return role in {"owner", "administrator"}

    def refresh(self) -> None:
        connection = self.app.db.cloud_connection()
        workspace_id = str(self.app.db.get_subscription().get("workspace_id") or "").strip()
        self.workspace_var.set(f"ID radnog prostora: {workspace_id or '-'}")
        usage = self.app.db.plan_usage()
        details = usage["details"]
        seats = usage["limits"].get("seats")
        seat_label = "Neograničeno" if seats is None else str(seats)
        revision = self.app.db.cloud_sync_state()["revision"]
        self.seats_var.set(f"{details['name']} | mesta: {usage['team_seats_used']} / {seat_label} | verzija: {revision}")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.members.clear()
        if not (workspace_id and connection.get("member_id") and connection.get("member_token")):
            self.status_var.set("Nalog vlasnika još nije uključen na ovom računaru.")
            return
        if not self._is_team_admin():
            self.status_var.set(f"Prijavljeni ste kao: {self.ROLE_LABELS.get(connection.get('member_role', ''), connection.get('member_role', ''))}.")
            self.tree.insert(
                "",
                "end",
                iid=connection["member_id"],
                values=(connection.get("member_name") or "", "", self.ROLE_LABELS.get(connection.get("member_role", ""), ""), "Aktivan", ""),
            )
            return
        try:
            result = self.app._team_client().team_members(
                workspace_id=workspace_id,
                member_id=connection["member_id"],
                member_token=connection["member_token"],
            )
        except CloudApiError as exc:
            self.status_var.set(f"Tim nije učitan: {exc}")
            return
        for index, member in enumerate(result.get("members") or []):
            member_id = str(member.get("id") or "")
            if not member_id:
                continue
            self.members[member_id] = member
            self.tree.insert(
                "",
                "end",
                iid=member_id,
                values=(
                    member.get("display_name") or "",
                    member.get("email") or "",
                    self.ROLE_LABELS.get(str(member.get("role") or ""), str(member.get("role") or "")),
                    self.STATUS_LABELS.get(str(member.get("status") or ""), str(member.get("status") or "")),
                    str(member.get("last_login_at") or "-").replace("T", " ")[:16],
                ),
                tags=(tree_row_tag(index),),
            )
        limit = result.get("seat_limit")
        self.seats_var.set(f"{details['name']} | mesta: {result.get('seats_used', 0)} / {'Neograničeno' if limit is None else limit} | verzija: {revision}")
        self.status_var.set("Centralni tim je ažuriran.")

    def _on_select(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        self.selected_member_id = selected[0] if selected else ""

    def setup_owner(self) -> None:
        connection = self.app.db.cloud_connection()
        if not connection.get("workspace_token"):
            messagebox.showwarning("OpsNest", "Prvo registrujte firmu da bi se otvorio centralni radni prostor.", parent=self)
            return
        TeamOwnerAccountDialog(self, self.app, on_ready=self.refresh)

    def invite(self) -> None:
        ready = self._connection()
        if not ready:
            return
        if not self._is_team_admin():
            messagebox.showwarning("OpsNest", "Samo vlasnik ili administrator mogu da šalju pozive.", parent=self)
            return
        name, email = self.name_var.get().strip(), self.email_var.get().strip().lower()
        if not name or "@" not in email:
            self.status_var.set("Unesite ime i važeći poslovni e-mail člana.")
            return
        connection, workspace_id = ready
        try:
            self.app._team_client().invite_team_member(
                workspace_id=workspace_id,
                member_id=connection["member_id"],
                member_token=connection["member_token"],
                display_name=name,
                email=email,
                role=self._role_code(self.role_var.get()),
            )
        except CloudApiError as exc:
            self.status_var.set(f"Poziv nije poslat: {exc}")
            return
        self.name_var.set("")
        self.email_var.set("")
        self.status_var.set("Poziv je poslat. Član dobija kod na e-mail i sam postavlja lozinku.")
        self.refresh()

    def revoke_selected(self) -> None:
        ready = self._connection()
        if not ready or not self.selected_member_id:
            self.status_var.set("Izaberite člana kome želite da ukinete pristup.")
            return
        if not self._is_team_admin():
            messagebox.showwarning("OpsNest", "Samo vlasnik ili administrator mogu da ukinu pristup.", parent=self)
            return
        target = self.members.get(self.selected_member_id, {})
        if target.get("role") == "owner":
            messagebox.showwarning("OpsNest", "Vlasnički pristup se ne može ukinuti iz ovog prozora.", parent=self)
            return
        if not messagebox.askyesno("OpsNest", f"Ukinuti pristup članu {target.get('display_name') or target.get('email')}?", parent=self):
            return
        connection, workspace_id = ready
        try:
            self.app._team_client().revoke_team_member(
                workspace_id=workspace_id,
                actor_member_id=connection["member_id"],
                actor_member_token=connection["member_token"],
                member_id=self.selected_member_id,
            )
        except CloudApiError as exc:
            self.status_var.set(f"Pristup nije ukinut: {exc}")
            return
        self.selected_member_id = ""
        self.status_var.set("Pristup je ukinut, a postojeće sesije tog člana su opozvane.")
        self.refresh()

    def download_data(self) -> None:
        if self.app.download_team_data(parent=self, confirm=True):
            self.status_var.set("Zajednički podaci su preuzeti. Lokalni prikaz je osvežen.")
            self.refresh()

    def upload_data(self) -> None:
        if self.app.upload_team_data(parent=self):
            self.status_var.set("Izmene su bezbedno poslate u zajednički radni prostor.")
            self.refresh()


class TeamOwnerAccountDialog(tk.Toplevel):
    """Sets the central owner's password without ever storing that password locally."""

    def __init__(self, master: tk.Widget, app: MainApp, *, on_ready: Callable[[], None]) -> None:
        super().__init__(master)
        self.app, self.on_ready = app, on_ready
        company = app.db.get_company()
        self.name_var = tk.StringVar(value=str(company.get("director_name") or company.get("name") or ""))
        self.password_var = tk.StringVar()
        self.confirm_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.title("Uključi centralni nalog")
        self.configure(background=BG)
        frame = ttk.Frame(self, style="App.TFrame", padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Centralni nalog vlasnika", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frame,
            text="Ovu lozinku zna samo vlasnik. OpsNest čuva bezbedan hash, a svaki računar dobija odvojenu sesiju koju možete kasnije opozvati.",
            style="Help.TLabel",
            wraplength=520,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 12))
        add_field(frame, 2, 0, "Ime vlasnika", self.name_var, width=34)
        add_field(frame, 3, 0, "Nova lozinka", self.password_var, width=34, show="*")
        add_field(frame, 4, 0, "Potvrdite lozinku", self.confirm_var, width=34, show="*")
        ttk.Label(frame, text="Najmanje 10 znakova.", style="Help.TLabel").grid(row=5, column=1, sticky="w")
        ttk.Label(frame, textvariable=self.status_var, style="Help.TLabel", wraplength=500).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        controls = ttk.Frame(frame, style="App.TFrame")
        controls.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Button(controls, text="Sačuvaj i uključi", style="Primary.TButton", command=self.submit).pack(side="left")
        ttk.Button(controls, text="Otkaži", command=self.destroy).pack(side="right")
        center_window(self, 610, 380)
        self.minsize(540, 340)

    def submit(self) -> None:
        name, password, confirm = self.name_var.get().strip(), self.password_var.get(), self.confirm_var.get()
        if not name:
            self.status_var.set("Unesite ime vlasnika.")
            return
        if password != confirm:
            self.status_var.set("Lozinke se ne podudaraju.")
            return
        connection = self.app.db.cloud_connection()
        workspace_id = str(self.app.db.get_subscription().get("workspace_id") or "").strip()
        try:
            result = self.app._team_client().setup_owner_account(
                workspace_id=workspace_id,
                workspace_token=connection["workspace_token"],
                display_name=name,
                password=password,
                device_name=platform.node() or "OpsNest Desktop",
            )
        except CloudApiError as exc:
            self.status_var.set(f"Centralni nalog nije uključen: {exc}")
            return
        self.app.db.save_cloud_member_session(
            member_id=str(result.get("member_id") or ""),
            member_token=str(result.get("member_token") or ""),
            member_role=str(result.get("member_role") or "owner"),
            member_name=str((result.get("member") or {}).get("display_name") or name),
        )
        self.on_ready()
        messagebox.showinfo("OpsNest", "Centralni nalog vlasnika je uključen. Sada možete pozvati članove tima.", parent=self)
        self.destroy()


class TeamSignInDialog(tk.Toplevel):
    """Joins an existing workspace through an invitation or normal member sign-in."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.login_workspace_var = tk.StringVar()
        self.login_email_var = tk.StringVar()
        self.login_password_var = tk.StringVar()
        self.invite_email_var = tk.StringVar()
        self.invite_code_var = tk.StringVar()
        self.invite_password_var = tk.StringVar()
        self.invite_confirm_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.title("Prijava u OpsNest tim")
        self.configure(background=BG)
        self._build()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        center_window(self, 680, 530)
        self.minsize(620, 480)
        self.bind("<Escape>", lambda _event: self.destroy())

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Prijava u zajednički tim", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Ako ste dobili poziv, unesite e-mail, šestocifreni kod i svoju novu lozinku. Za sledeće prijave koristite ID radnog prostora, e-mail i lozinku.",
            style="Help.TLabel",
            wraplength=620,
        ).pack(anchor="w", pady=(5, 12))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        login = ttk.Frame(notebook, style="App.TFrame", padding=14)
        invite = ttk.Frame(notebook, style="App.TFrame", padding=14)
        notebook.add(login, text="Prijava")
        notebook.add(invite, text="Prihvati poziv")
        for frame in (login, invite):
            frame.columnconfigure(1, weight=1)
        add_field(login, 0, 0, "ID radnog prostora", self.login_workspace_var, width=42)
        add_field(login, 1, 0, "E-mail", self.login_email_var, width=42)
        add_field(login, 2, 0, "Lozinka", self.login_password_var, width=42, show="*")
        ttk.Button(login, text="Prijavi se i preuzmi podatke", style="Primary.TButton", command=self.sign_in).grid(row=3, column=1, sticky="w", pady=(16, 0))
        add_field(invite, 0, 0, "E-mail iz poziva", self.invite_email_var, width=42)
        add_field(invite, 1, 0, "Kod iz e-maila", self.invite_code_var, width=42)
        add_field(invite, 2, 0, "Nova lozinka", self.invite_password_var, width=42, show="*")
        add_field(invite, 3, 0, "Potvrdite lozinku", self.invite_confirm_var, width=42, show="*")
        ttk.Button(invite, text="Prihvati poziv i preuzmi podatke", style="Primary.TButton", command=self.accept_invite).grid(row=4, column=1, sticky="w", pady=(16, 0))
        ttk.Label(outer, textvariable=self.status_var, style="Help.TLabel", wraplength=620).pack(anchor="w", pady=(10, 0))
        ttk.Button(outer, text="Zatvori", command=self.destroy).pack(anchor="e", pady=(10, 0))

    def _finish(self, result: dict[str, Any]) -> None:
        member = result.get("member") or {}
        workspace_id = str(result.get("workspace_id") or "").strip()
        if not workspace_id:
            self.status_var.set("Server nije vratio ID radnog prostora.")
            return
        self.app.db.link_team_workspace(
            workspace_id=workspace_id,
            api_url=OPSNEST_CLOUD_API_URL,
            member_id=str(result.get("member_id") or ""),
            member_token=str(result.get("member_token") or ""),
            member_role=str(result.get("member_role") or member.get("role") or "operator"),
            member_name=str(member.get("display_name") or ""),
        )
        self.destroy()
        if self.app.download_team_data(parent=self.app, confirm=True, allow_empty_owner_workspace=True):
            self.app.activate_workspace()
            messagebox.showinfo(
                "OpsNest",
                "Prijava je uspešna. Ako je radni prostor bio prazan, sada možete uneti početni profil firme; "
                "u suprotnom su zajednički podaci preuzeti na ovaj računar.",
                parent=self.app,
            )

    def sign_in(self) -> None:
        workspace_id, email, password = self.login_workspace_var.get().strip(), self.login_email_var.get().strip(), self.login_password_var.get()
        if not workspace_id or "@" not in email or not password:
            self.status_var.set("Unesite ID radnog prostora, e-mail i lozinku.")
            return
        try:
            self._finish(
                OpsNestCloudClient(OPSNEST_CLOUD_API_URL).team_login(
                    workspace_id=workspace_id,
                    email=email,
                    password=password,
                    device_name=platform.node() or "OpsNest Desktop",
                )
            )
        except CloudApiError as exc:
            self.status_var.set(f"Prijava nije uspela: {exc}")

    def accept_invite(self) -> None:
        email, code = self.invite_email_var.get().strip(), self.invite_code_var.get().strip()
        password, confirm = self.invite_password_var.get(), self.invite_confirm_var.get()
        if "@" not in email or len(code) != 6 or not code.isdigit():
            self.status_var.set("Unesite e-mail i šestocifreni kod iz poziva.")
            return
        if password != confirm:
            self.status_var.set("Lozinke se ne podudaraju.")
            return
        try:
            self._finish(
                OpsNestCloudClient(OPSNEST_CLOUD_API_URL).accept_team_invitation(
                    email=email,
                    code=code,
                    password=password,
                    device_name=platform.node() or "OpsNest Desktop",
                )
            )
        except CloudApiError as exc:
            self.status_var.set(f"Poziv nije prihvaćen: {exc}")


class SupportDiagnosticsDialog(tk.Toplevel):
    """Collect a support note in a readable, privacy-first dialog."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.title(tr("OpsNest podrška"))
        self.configure(background=BG)
        self.resizable(True, True)
        self._build()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        localize_widget_tree(self, active_ui_language())
        center_window(self, 760, 560)
        self.minsize(640, 470)
        self.note_text.focus_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-Return>", lambda _event: self.submit())

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=22)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(6, weight=1)

        ttk.Label(outer, text="OpsNest podrška", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text="Kako podrška može da pomogne?", style="CardTitle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(12, 3)
        )
        ttk.Label(
            outer,
            text=(
                "Pošaljite kratak opis problema. Ako se odnosi na dokument, navedite broj fakture ili projekta, "
                "ali ne unosite lozinke, PIN-ove ili podatke kartice."
            ),
            style="Help.TLabel",
            justify="left",
            wraplength=700,
        ).grid(row=2, column=0, sticky="ew")

        privacy = ttk.LabelFrame(outer, text="Šta se bezbedno šalje podršci", padding=14)
        privacy.grid(row=3, column=0, sticky="ew", pady=(16, 14))
        privacy.columnconfigure(0, weight=1)
        ttk.Label(
            privacy,
            text="Samo verzija aplikacije, operativni sistem, status licence i opis koji ovde unesete.",
            style="Value.TLabel",
            justify="left",
            wraplength=660,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            privacy,
            text="Fakture, PDF-ovi, prilozi, lozinke, PIN-ovi, bankovni i kartični podaci nikada se ne šalju.",
            style="Help.TLabel",
            justify="left",
            wraplength=660,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        ttk.Label(outer, text="Opis problema (opciono)", style="Field.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 6))
        self.note_text = tk.Text(
            outer,
            height=10,
            wrap="word",
            background="white",
            foreground=TEXT,
            insertbackground=TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
            font=(preferred_ui_font(self), 10),
        )
        self.note_text.grid(row=5, column=0, sticky="nsew")

        controls = ttk.Frame(outer, style="App.TFrame")
        controls.grid(row=6, column=0, sticky="ew", pady=(16, 0))
        ttk.Button(controls, text="Zatvori", command=self.destroy).pack(side="right")
        ttk.Button(
            controls,
            text="Pošalji bezbednu dijagnostiku",
            style="Primary.TButton",
            command=self.submit,
        ).pack(side="right", padx=(0, 8))

    def submit(self) -> None:
        note = self.note_text.get("1.0", "end-1c").strip()
        if self.app.send_safe_diagnostics(note):
            self.destroy()


class PlanAndBillingDialog(tk.Toplevel):
    """The single, always-available place for trial, plans, payment and support."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.summary: dict[str, Any] = {}
        self.title("Moj paket i plaćanje")
        self.configure(background=BG)
        self.resizable(True, True)
        self._build()
        self.refresh()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        center_window(self, 970, 710)
        self.minsize(820, 590)
        localize_widget_tree(self, active_ui_language())
        self.bind("<Escape>", lambda _event: self.destroy())

    @staticmethod
    def _date_text(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return "-"
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return raw.replace("T", " ")

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        ttk.Label(outer, text="Moj paket i plaćanje", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.trial_explainer_var = tk.StringVar(value=plan_dialog_copy("trial_explainer"))
        ttk.Label(
            outer,
            textvariable=self.trial_explainer_var,
            style="Help.TLabel",
            wraplength=900,
        ).grid(row=1, column=0, sticky="w", pady=(3, 12))

        overview = ttk.LabelFrame(outer, text="Status licence", padding=12)
        overview.grid(row=2, column=0, sticky="ew")
        overview.columnconfigure(1, weight=1)
        self.status_var = tk.StringVar()
        self.plan_var = tk.StringVar()
        self.feature_plan_var = tk.StringVar()
        self.trial_start_var = tk.StringVar()
        self.trial_end_var = tk.StringVar()
        self.next_billing_var = tk.StringVar()
        self.ai_addon_var = tk.StringVar()
        for row, (label, var) in enumerate((
            ("Status", self.status_var),
            ("Kupovni paket", self.plan_var),
            ("Funkcije trenutno", self.feature_plan_var),
            ("Početak probe", self.trial_start_var),
            ("Kraj probe", self.trial_end_var),
            ("Sledeća naplata", self.next_billing_var),
            ("AI dodatak", self.ai_addon_var),
        )):
            ttk.Label(overview, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 14), pady=2)
            ttk.Label(overview, textvariable=var, style="CardTitle.TLabel").grid(row=row, column=1, sticky="w", pady=2)

        middle = ttk.Frame(outer, style="App.TFrame")
        middle.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        middle.columnconfigure(0, weight=1)
        middle.columnconfigure(1, weight=2)
        middle.rowconfigure(0, weight=1)

        usage = ttk.LabelFrame(middle, text="Iskorišćenost ovog meseca", padding=12)
        usage.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.usage_vars = {key: tk.StringVar(value="-") for key in ("seats", "projects", "issued_invoices", "pdf_imports")}
        for row, (key, label) in enumerate((
            ("seats", "Korisnička mesta"),
            ("projects", "Aktivni projekti"),
            ("issued_invoices", "Izdate fakture"),
            ("pdf_imports", "PDF uvozi"),
        )):
            ttk.Label(usage, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            ttk.Label(usage, textvariable=self.usage_vars[key], style="Value.TLabel").grid(row=row, column=1, sticky="e", padx=(16, 0), pady=4)

        plans = ttk.LabelFrame(middle, text="OpsNest paketi", padding=10)
        plans.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.card_price_vars: dict[str, tk.StringVar] = {}
        self.card_limits_vars: dict[str, tk.StringVar] = {}
        for index, code in enumerate(("starter", "business", "pro")):
            plans.columnconfigure(index, weight=1)
            detail = plan_details(code)
            card = ttk.Frame(plans, style="Panel.TFrame", padding=10, relief="solid", borderwidth=1)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 5, 0))
            ttk.Label(card, text=detail["name"], style="CardTitle.TLabel").pack(anchor="w")
            self.card_price_vars[code] = tk.StringVar()
            self.card_limits_vars[code] = tk.StringVar()
            ttk.Label(card, textvariable=self.card_price_vars[code], style="Value.TLabel").pack(anchor="w", pady=(4, 8))
            ttk.Label(card, textvariable=self.card_limits_vars[code], style="Help.TLabel", justify="left", wraplength=170).pack(anchor="w", pady=(0, 10))
            ttk.Button(card, text="Izaberi paket", style="Primary.TButton", command=lambda choice=code: self.choose_plan(choice)).pack(anchor="w")

        self.ai_addon_buttons: dict[str, ttk.Button] = {}
        for index, (code, label) in enumerate((
            ("ai_starter", "AI Starter · €4,90 · 100 saveta"),
            ("ai_business", "AI Business · €8,90 · 200 saveta"),
            ("ai_pro", "AI Pro · €12,90 · 300 saveta"),
        )):
            button = ttk.Button(plans, text=label, style="Primary.TButton", command=lambda choice=code: self.choose_ai_advisor(choice))
            button.grid(row=1, column=index, sticky="ew", padx=(0 if index == 0 else 5, 0), pady=(12, 0))
            self.ai_addon_buttons[code] = button

        privacy = ttk.Label(
            outer,
            text="Bezbedna dijagnostika nikada ne šalje fakture, PDF-ove, priloge, lozinke, PIN ili podatke o plaćanju.",
            style="Help.TLabel",
            wraplength=900,
        )
        privacy.grid(row=4, column=0, sticky="w", pady=(12, 4))
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Aktiviraj / potvrdi e-mail", command=self.activate_email).pack(side="left")
        ttk.Button(buttons, text="Osveži status", command=self.refresh_online).pack(side="left", padx=6)
        ttk.Button(buttons, text="Proveri PayPal Live", command=self.test_paypal_live).pack(side="left", padx=6)
        ttk.Button(buttons, text="Korisnici firme", command=self.app.open_team_members).pack(side="left")
        ttk.Button(buttons, text="Otkazivanje u PayPal-u", command=self.open_cancellation).pack(side="left", padx=6)
        ttk.Button(buttons, text="Pošalji dijagnostiku podršci", command=self.send_diagnostics).pack(side="left", padx=6)
        ttk.Button(buttons, text="Proveri ažuriranja", command=self.check_updates).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cenovnik na sajtu", command=lambda: webbrowser.open_new_tab(OPSNEST_PRICING_URL)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        subscription = self.app.db.get_subscription()
        usage = self.app.db.plan_usage()
        self.trial_explainer_var.set(plan_dialog_copy("trial_explainer"))
        status = str(subscription.get("status") or "not_started").lower()
        purchased = str(usage.get("purchased_code") or "starter")
        effective = str(usage.get("effective_code") or purchased)
        self.status_var.set(self.app.subscription_status_text())
        self.plan_var.set(plan_details(purchased)["name"])
        self.feature_plan_var.set(plan_details(effective)["name"])
        self.trial_start_var.set(self._date_text(subscription.get("trial_started_at")))
        self.trial_end_var.set(self._date_text(subscription.get("trial_ends_at")))
        self.next_billing_var.set(self._date_text(self.summary.get("next_billing_at")) if self.summary else "-")
        ai_advisor = dict(self.summary.get("ai_advisor") or {})
        if bool(ai_advisor.get("enabled")):
            self.ai_addon_var.set(f"{ai_advisor.get('tier_name', 'AI savetnik')} aktivan · {ai_advisor.get('requests_remaining', 0)} / {ai_advisor.get('monthly_requests', 100)} saveta preostalo")
            for button in self.ai_addon_buttons.values():
                button.configure(state="disabled")
        else:
            self.ai_addon_var.set("Nije aktivan · opcioni dodatak za Starter, Business i Pro")
            for button in self.ai_addon_buttons.values():
                button.configure(state="normal")
        for key, var in self.usage_vars.items():
            usage_key = {"seats": "team_seats_used", "projects": "active_projects", "issued_invoices": "issued_invoices", "pdf_imports": "pdf_imports"}[key]
            limit_key = {"seats": "seats", "projects": "projects", "issued_invoices": "issued_invoices_per_month", "pdf_imports": "pdf_imports_per_month"}[key]
            limit = (usage.get("limits") or {}).get(limit_key)
            label_limit = tr("Neograničeno") if limit is None else str(limit)
            var.set(f"{usage.get(usage_key, 0)} / {label_limit}")
        for code, price_var in self.card_price_vars.items():
            detail = plan_details(code)
            unlimited = plan_dialog_copy("unlimited")
            projects = unlimited if detail["projects"] is None else str(detail["projects"])
            invoices = unlimited if detail["issued_invoices_per_month"] is None else str(detail["issued_invoices_per_month"])
            pdfs = unlimited if detail["pdf_imports_per_month"] is None else str(detail["pdf_imports_per_month"])
            seats = unlimited if detail.get("seats") is None else str(detail["seats"])
            price_var.set(f"{detail['price_eur']} {plan_dialog_copy('per_month')}")
            self.card_limits_vars[code].set(plan_dialog_copy(
                "card_limits",
                projects=projects,
                invoices=invoices,
                pdfs=pdfs,
                seats=seats,
            ))
        if status == "not_started":
            self.status_var.set(plan_dialog_copy("not_registered"))

    def _run(self, action: Callable[[], dict[str, Any]], success: Callable[[dict[str, Any]], None]) -> None:
        results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        def worker() -> None:
            try:
                results.put((True, action()))
            except Exception as exc:
                results.put((False, exc))
        def finish() -> None:
            try:
                ok, result = results.get_nowait()
            except queue.Empty:
                self.after(80, finish)
                return
            if not self.winfo_exists():
                return
            if not ok:
                messagebox.showerror("OpsNest", str(result), parent=self)
                return
            success(result)
        threading.Thread(target=worker, daemon=True).start()
        self.after(80, finish)

    def refresh_online(self) -> None:
        connection = self.app.db.cloud_connection()
        subscription = self.app.db.get_subscription()
        if not connection["workspace_token"]:
            if self.app.active_team_role():
                messagebox.showinfo(
                    "OpsNest tim",
                    "Status pretplate prikazuje vlasnik firme. Vaš pristup i funkcije preuzimaju se kroz zajednički radni prostor.",
                    parent=self,
                )
                return
            self.activate_email()
            return
        def action() -> dict[str, Any]:
            client = OpsNestCloudClient(OPSNEST_CLOUD_API_URL)
            return client.billing_summary(workspace_id=str(subscription["workspace_id"]), workspace_token=connection["workspace_token"])
        def success(result: dict[str, Any]) -> None:
            self.summary = result
            self.app.db.apply_subscription_update(
                status=str(result.get("status") or "verification_pending"),
                plan_code=str(result.get("plan_code") or "starter"),
                billing_provider="opsnest_cloud",
                verified_at=datetime.now().isoformat(timespec="seconds"),
                trial_started_at=str(result.get("trial_started_at") or ""),
                trial_ends_at=str(result.get("trial_ends_at") or ""),
            )
            self.app.refresh_all()
            self.refresh()
        self._run(action, success)

    def test_paypal_live(self) -> None:
        """Validate PayPal credentials and plan IDs without creating a subscription."""
        if not self.app.require_team_permission({"owner"}, "provera PayPal naplate", parent=self):
            return
        connection = self.app.db.cloud_connection()
        subscription = self.app.db.get_subscription()
        if not connection["workspace_token"]:
            self.activate_email()
            return

        def action() -> dict[str, Any]:
            return OpsNestCloudClient(OPSNEST_CLOUD_API_URL).billing_readiness(
                workspace_id=str(subscription["workspace_id"]),
                workspace_token=connection["workspace_token"],
            )

        def success(result: dict[str, Any]) -> None:
            mode = str(result.get("mode") or "").lower()
            if bool(result.get("ready")) and mode == "live":
                messagebox.showinfo(
                    "PayPal Live",
                    "PayPal Live je povezan: kredencijali i sva tri paketa su potvrđeni. Nije napravljena nikakva pretplata niti naplata.",
                    parent=self,
                )
                return
            messagebox.showwarning(
                "PayPal Live",
                "PayPal Live još nije spreman za naplatu. Proverite da Render ima PAYPAL_MODE=live, Live Client ID, Live Client Secret, Webhook ID i tri Live Plan ID vrednosti.",
                parent=self,
            )

        self._run(action, success)

    def choose_plan(self, plan_code: str) -> None:
        if not self.app.require_team_permission({"owner"}, "promena paketa i plaćanje", parent=self):
            return
        self.app.open_plan_checkout(plan_code=plan_code)

    def choose_ai_advisor(self, addon_code: str) -> None:
        if not self.app.require_team_permission({"owner"}, "aktivacija AI savetnika", parent=self):
            return
        self.app.open_plan_checkout(plan_code=addon_code)

    def activate_email(self) -> None:
        if not self.app.require_team_permission({"owner"}, "aktivacija firme", parent=self):
            return
        self.app.open_online_activation()

    def open_cancellation(self) -> None:
        if not self.app.require_team_permission({"owner"}, "otkazivanje pretplate", parent=self):
            return
        url = str(self.summary.get("cancellation_url") or OPSNEST_PAYPAL_CANCELLATION_URL)
        webbrowser.open_new_tab(url)

    def send_diagnostics(self) -> None:
        SupportDiagnosticsDialog(self, self.app)

    def check_updates(self) -> None:
        def action() -> dict[str, Any]:
            return OpsNestCloudClient(OPSNEST_CLOUD_API_URL).desktop_update()
        def success(result: dict[str, Any]) -> None:
            latest = str(result.get("latest_version") or "").strip()
            installer_url = str(result.get("installer_url") or "").strip()
            installer_sha256 = str(result.get("installer_sha256") or "").strip().lower()
            if not latest or latest == "0.0.0" or version_key(latest) <= version_key(OPSNEST_APP_VERSION):
                messagebox.showinfo(tr("OpsNest ažuriranje"), plan_dialog_copy("latest_update", version=OPSNEST_APP_VERSION), parent=self)
            elif installer_url.startswith("https://") and re.fullmatch(r"[a-f0-9]{64}", installer_sha256):
                DesktopUpdateDialog(self, self.app, latest, installer_url, installer_sha256)
            elif installer_url.startswith("https://") and messagebox.askyesno(
                tr("OpsNest ažuriranje"),
                plan_dialog_copy("update_metadata_pending", version=latest),
                parent=self,
            ):
                webbrowser.open_new_tab(installer_url)
            else:
                messagebox.showinfo(tr("OpsNest ažuriranje"), plan_dialog_copy("installer_pending", version=latest), parent=self)
        self._run(action, success)


class DesktopUpdateDialog(tk.Toplevel):
    """Download a verified official installer without sending a user to a browser."""

    def __init__(self, master: tk.Widget, app: MainApp, version: str, installer_url: str, sha256: str) -> None:
        super().__init__(master)
        self.app = app
        self.version = version
        self.installer_url = installer_url
        self.sha256 = sha256.lower()
        self.installer_path: Path | None = None
        self._downloading = False
        self.title(tr("OpsNest ažuriranje"))
        self.configure(background=BG)
        self.resizable(False, False)
        outer = ttk.Frame(self, style="App.TFrame", padding=22)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text=tr("OpsNest ažuriranje"), style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text=plan_dialog_copy("update_ready", version=version),
            style="Help.TLabel",
            wraplength=540,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 14))
        safety = ttk.LabelFrame(outer, text=plan_dialog_copy("update_safety_title"), padding=12)
        safety.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            safety,
            text=plan_dialog_copy("update_safety"),
            style="Value.TLabel",
            wraplength=510,
            justify="left",
        ).pack(anchor="w")

        self.status_var = tk.StringVar(value=plan_dialog_copy("update_waiting"))
        ttk.Label(outer, textvariable=self.status_var, style="Help.TLabel", wraplength=540).grid(
            row=3, column=0, sticky="w", pady=(16, 5)
        )
        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.progress.grid(row=4, column=0, sticky="ew")

        controls = ttk.Frame(outer, style="App.TFrame")
        controls.grid(row=5, column=0, sticky="ew", pady=(18, 0))
        self.install_button = ttk.Button(
            controls,
            text=plan_dialog_copy("download_install"),
            style="Primary.TButton",
            command=self.download,
        )
        self.install_button.pack(side="left")
        ttk.Button(controls, text=tr("Otkaži"), command=self.destroy).pack(side="right")

        self.transient(master.winfo_toplevel())
        self.grab_set()
        center_window(self, 640, 355)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

    def _close(self) -> None:
        if self._downloading:
            return
        self.destroy()

    def _set_progress(self, downloaded: int, total: int) -> None:
        if not self.winfo_exists():
            return
        if total > 0:
            percent = min(100, (downloaded * 100) / total)
            self.progress.configure(mode="determinate", value=percent)
            self.status_var.set(plan_dialog_copy("update_downloading", percent=int(percent)))
        else:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self.status_var.set(plan_dialog_copy("update_downloading_unknown"))

    def download(self) -> None:
        if self._downloading:
            return
        self._downloading = True
        self.install_button.configure(state="disabled")
        self.status_var.set(plan_dialog_copy("update_downloading", percent=0))

        def worker() -> None:
            try:
                safe_version = re.sub(r"[^0-9A-Za-z._-]", "", self.version) or "latest"
                cache_dir = update_cache_dir()
                cache_dir.mkdir(parents=True, exist_ok=True)
                final_path = cache_dir / f"OpsNest-Setup-{safe_version}.exe"
                part_path = final_path.with_suffix(".part")
                digest = hashlib.sha256()
                downloaded = 0
                request = Request(self.installer_url, headers={"User-Agent": f"OpsNest/{OPSNEST_APP_VERSION}"})
                with urlopen(request, timeout=45) as response, part_path.open("wb") as target:
                    total = int(response.headers.get("Content-Length") or 0)
                    for chunk in iter(lambda: response.read(1024 * 512), b""):
                        target.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        self.after(0, self._set_progress, downloaded, total)
                actual_hash = digest.hexdigest().lower()
                if actual_hash != self.sha256:
                    part_path.unlink(missing_ok=True)
                    raise ValueError(plan_dialog_copy("update_integrity_failed"))
                part_path.replace(final_path)
                self.after(0, self._download_finished, final_path)
            except Exception as exc:
                self.after(0, self._download_failed, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _download_failed(self, detail: str) -> None:
        if not self.winfo_exists():
            return
        self._downloading = False
        self.progress.stop()
        self.install_button.configure(state="normal")
        self.status_var.set(plan_dialog_copy("update_download_failed"))
        messagebox.showerror(tr("OpsNest ažuriranje"), detail, parent=self)

    def _download_finished(self, installer_path: Path) -> None:
        if not self.winfo_exists():
            return
        self._downloading = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.installer_path = installer_path
        self.status_var.set(plan_dialog_copy("update_downloaded"))
        self.install_button.configure(text=plan_dialog_copy("install_restart"), state="normal", command=self.install)

    def install(self) -> None:
        if self.installer_path is None:
            return
        if not messagebox.askyesno(
            tr("OpsNest ažuriranje"),
            plan_dialog_copy("update_restart_question", version=self.version),
            parent=self,
        ):
            return
        if self.app.launch_auto_update(self.installer_path):
            self.destroy()


class OnlineActivationDialog(tk.Toplevel):
    """Single-window activation flow: e-mail, code, and trial start stay in OpsNest."""

    def __init__(
        self,
        master: tk.Widget,
        app: MainApp,
        *,
        client: OpsNestCloudClient,
        api_url: str,
        workspace_id: str,
        prefill_company: str = "",
        prefill_email: str = "",
    ) -> None:
        super().__init__(master)
        self.app = app
        self.client = client
        self.api_url = api_url
        self.workspace_id = workspace_id
        # Details are blank on a fresh installation. They are passed explicitly
        # only immediately after this user has just saved their own registration.
        self.company_var = tk.StringVar(value=str(prefill_company or "").strip())
        self.email_var = tk.StringVar(value=str(prefill_email or "").strip())
        self.code_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value=tr("Unesite poslovni e-mail i kod. Ako još nemate kod, kliknite Pošalji kod.")
        )
        self._busy = False

        self.title(tr("Aktiviraj OpsNest"))
        self.configure(background=BG)
        self.resizable(False, False)
        outer = ttk.Frame(self, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="Aktiviraj OpsNest", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text="Sve završavate ovde, bez browsera. Lokalna proba počinje pri registraciji firme; potvrdom e-maila je bezbedno povezujete sa plaćanjem i podrškom.",
            style="Help.TLabel",
            wraplength=500,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 15))

        company_entry = add_field(outer, 2, 0, "Naziv firme", self.company_var, width=38)
        email_entry = add_field(outer, 3, 0, "Poslovni e-mail", self.email_var, width=38)
        self.code_entry = add_field(outer, 4, 0, "Kod sa e-maila", self.code_var, width=18)
        self.code_entry.configure(state="normal", takefocus=True)
        ttk.Label(
            outer,
            text="Kod važi 15 minuta. Nakon 5 pogrešnih unosa tražite novi kod.",
            style="Help.TLabel",
            wraplength=500,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.status_label = ttk.Label(outer, textvariable=self.status_var, style="Help.TLabel", wraplength=500)
        self.status_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        self.send_button = ttk.Button(buttons, text="Pošalji kod", style="Primary.TButton", command=self.send_code)
        self.send_button.pack(side="left")
        self.activate_button = ttk.Button(buttons, text="Potvrdi i aktiviraj", command=self.confirm_code)
        self.activate_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="Kasnije", command=self.destroy).pack(side="right")

        self.transient(master.winfo_toplevel())
        self.grab_set()
        center_window(self, 560, 410)
        localize_widget_tree(self, active_ui_language())
        company_entry.focus_set()
        self.bind("<Return>", lambda _event: self.confirm_code() if str(self.code_var.get()).strip() else self.send_code())
        self.bind("<Escape>", lambda _event: self.destroy())

    def _set_busy(self, value: bool) -> None:
        self._busy = value
        state = "disabled" if value else "normal"
        self.send_button.configure(state=state)
        self.activate_button.configure(state=state)

    def _run_request(self, action: Callable[[], dict[str, Any]], on_success: Callable[[dict[str, Any]], None]) -> None:
        if self._busy:
            return
        self._set_busy(True)
        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                result_queue.put((True, action()))
            except Exception as exc:  # The UI converts transport failures into a friendly status line.
                result_queue.put((False, exc))

        def receive_result() -> None:
            if not self.winfo_exists():
                return
            try:
                ok, result = result_queue.get_nowait()
            except queue.Empty:
                self.after(80, receive_result)
                return
            self._set_busy(False)
            if not ok:
                message = str(result) if isinstance(result, CloudApiError) else "Online servis trenutno nije dostupan. Pokušajte ponovo."
                self.status_var.set(message)
                return
            on_success(result)

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, receive_result)

    def send_code(self) -> None:
        company_name = self.company_var.get().strip()
        email = self.email_var.get().strip().lower()
        if len(company_name) < 2:
            self.status_var.set(tr("Unesite naziv firme."))
            return
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            self.status_var.set(tr("Unesite ispravan poslovni e-mail."))
            return
        self.status_var.set(tr("Šaljem verifikacioni kod..."))
        self._run_request(
            lambda: self.client.request_email_code(
                workspace_id=self.workspace_id,
                company_name=company_name,
                email=email,
            ),
            self._on_code_sent,
        )

    def _on_code_sent(self, _result: dict[str, Any]) -> None:
        self.code_entry.configure(state="normal", takefocus=True)
        self.code_entry.focus_set()
        self.status_var.set(tr("Kod je poslat. Upišite šest cifara iz e-maila i kliknite Potvrdi i aktiviraj."))

    def confirm_code(self) -> None:
        company_name = self.company_var.get().strip()
        email = self.email_var.get().strip().lower()
        code = self.code_var.get().strip()
        if len(company_name) < 2:
            self.status_var.set(tr("Unesite naziv firme."))
            return
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            self.status_var.set(tr("Unesite ispravan poslovni e-mail."))
            return
        if not (code.isdigit() and len(code) == 6):
            self.status_var.set(tr("Unesite šestocifreni kod sa e-maila."))
            return
        self.status_var.set(tr("Potvrđujem kod i aktiviram probni period..."))
        self._run_request(
            lambda: self.client.confirm_email_code(workspace_id=self.workspace_id, email=email, code=code),
            self._on_activation_confirmed,
        )

    def _on_activation_confirmed(self, result: dict[str, Any]) -> None:
        license_data = result.get("license") if isinstance(result.get("license"), dict) else {}
        token = str(result.get("workspace_token") or "")
        if not token or not license_data:
            self.status_var.set(tr("Online servis nije vratio potvrdu licence. Pokušajte ponovo."))
            return
        email = self.email_var.get().strip().lower()
        self.app.db.save_cloud_connection(api_url=self.api_url, workspace_token=token, owner_email=email)
        self.app.db.apply_subscription_update(
            status=str(license_data.get("status") or "verification_pending"),
            plan_code=str(license_data.get("plan_code") or "starter"),
            billing_provider="opsnest_cloud",
            verified_at=str(license_data.get("last_verified_at") or datetime.now().isoformat(timespec="seconds")),
            trial_started_at=str(license_data.get("trial_started_at") or ""),
            trial_ends_at=str(license_data.get("trial_ends_at") or ""),
        )
        self.app.refresh_subscription_status_indicator()
        self.destroy()
        messagebox.showinfo(
            tr("Aktivacija završena"),
            tr("Online licenca je potvrđena. Probni period traje 7 dana bez kartice."),
            parent=self.app,
        )


class CompanyProfileDialog(tk.Toplevel):
    """Full company editor opened from the project-first home page."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.title("Podaci firme")
        self.configure(background=BG)
        self.resizable(True, True)

        content = ttk.Frame(self, style="App.TFrame")
        content.pack(side="top", fill="both", expand=True)
        self.editor = CompanyTab(content, app)
        self.editor.pack(fill="both", expand=True)
        self.editor.refresh()

        footer = ttk.Frame(self, style="App.TFrame", padding=(12, 0, 12, 12))
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="Zatvori", command=self.destroy).pack(side="right")

        self.transient(master.winfo_toplevel())
        self.grab_set()
        maximize_large_window(self, minimum_width=940, minimum_height=620)
        self.bind("<Escape>", lambda event: self.destroy())


class CompanyRegistrationDialog(tk.Toplevel):
    """Create or update a company profile and its local sign-in credentials."""

    def __init__(self, master: tk.Widget, app: MainApp, *, from_access: bool = False) -> None:
        super().__init__(master)
        self.app = app
        self.from_access = from_access
        self.company = app.db.get_company()
        self.subscription = app.db.get_subscription()
        self.title("Registracija firme")
        self.configure(background=BG)
        self.resizable(True, True)
        self.vars = {
            "name": tk.StringVar(value=str(self.company.get("name") or "")),
            "eik": tk.StringVar(value=str(self.company.get("eik") or "")),
            "vat_number": tk.StringVar(value=str(self.company.get("vat_number") or "")),
            "address": tk.StringVar(value=str(self.company.get("address") or "")),
            "phone": tk.StringVar(value=str(self.company.get("phone") or "")),
            "email": tk.StringVar(value=str(self.company.get("email") or "")),
            "bank_name": tk.StringVar(value=str(self.company.get("bank_name") or "")),
            "iban": tk.StringVar(value=str(self.company.get("iban") or "")),
            "bic": tk.StringVar(value=str(self.company.get("bic") or "")),
            "director_name": tk.StringVar(value=str(self.company.get("director_name") or "")),
            "logo_path": tk.StringVar(value=str(self.company.get("logo_path") or "")),
            "business_profile": tk.StringVar(value=business_profile_label(self.company.get("business_profile") or "general")),
            "country_code": tk.StringVar(value=country_option_label(self.company.get("country_code") or "OTHER")),
            "default_currency": tk.StringVar(value=str(self.company.get("default_currency") or DEFAULT_CURRENCY)),
            "default_vat_rate": tk.StringVar(value=str(self.company.get("default_vat_rate") or "0.20")),
            "vat_regime": tk.StringVar(value=vat_regime_label(self.company.get("vat_regime") or "standard")),
            "einvoice_route": tk.StringVar(value=einvoice_route_label(self.company.get("einvoice_route") or "automatic")),
            "payment_term_days": tk.StringVar(value=str(self.company.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)),
            "issue_place": tk.StringVar(value=str(self.company.get("issue_place") or "")),
            "ui_language": tk.StringVar(value=language_label(self.company.get("ui_language"))),
            "login_email": tk.StringVar(value=str(self.company.get("login_email") or self.company.get("email") or "")),
            "login_pin": tk.StringVar(),
            "login_pin_confirm": tk.StringVar(),
        }
        self._build()
        localize_widget_tree(self, active_ui_language())
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda event: self.postpone())
        self.protocol("WM_DELETE_WINDOW", self.postpone)
        center_window(self, 790, 710)

    def _build(self) -> None:
        shell = ttk.Frame(self, style="App.TFrame", padding=(16, 16, 16, 8))
        shell.pack(fill="both", expand=True)
        form_scroll = ScrollableFrame(shell)
        form_scroll.pack(fill="both", expand=True)
        outer = form_scroll.inner
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="Registracija firme", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text="Podesite poslovni profil, državu, valutu i PDV režim. Ovi podaci se koriste kao podrazumevani na novim fakturama.",
            style="Help.TLabel",
            wraplength=720,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))

        details_row = 2
        if self.subscription.get("status") == "not_started":
            ttk.Label(outer, text=subscription_copy("not_started"), style="Help.TLabel", wraplength=720).grid(
                row=2, column=0, columnspan=2, sticky="w", pady=(0, 10)
            )
            details_row = 3

        left = ttk.LabelFrame(outer, text="Podaci firme", padding=10)
        left.grid(row=details_row, column=0, sticky="nsew", padx=(0, 7))
        left.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate([
            ("name", "Naziv"),
            ("eik", "EIK / BULSTAT"),
            ("vat_number", "PDV broj"),
            ("address", "Adresa"),
            ("phone", "Telefon"),
            ("email", "E-mail"),
            ("director_name", "Direktor"),
            ("logo_path", "Logo putanja"),
        ]):
            add_field(left, row, 0, label, self.vars[key], width=29)
            if key == "logo_path":
                ttk.Button(left, text="Izaberi", command=self.browse_logo).grid(row=row, column=2, sticky="w", padx=4)

        right = ttk.LabelFrame(outer, text="Podešavanja fakture", padding=10)
        right.grid(row=details_row, column=1, sticky="nsew", padx=(7, 0))
        right.columnconfigure(1, weight=1)
        add_field(right, 0, 0, "Banka", self.vars["bank_name"], width=29)
        add_field(right, 1, 0, "IBAN", self.vars["iban"], width=29)
        add_field(right, 2, 0, "BIC / SWIFT", self.vars["bic"], width=29)
        add_combo(right, 3, 0, "Delatnost", self.vars["business_profile"], list(BUSINESS_PROFILE_LABELS.values()), width=34)
        self.country_combo = add_combo(right, 4, 0, "Država registracije", self.vars["country_code"], country_option_values(), width=24)
        self.country_combo.bind("<<ComboboxSelected>>", self._apply_country_vat_default)
        ttk.Label(right, text="Država predlaže valutu i standardnu PDV stopu. Potvrdite PDV režim sa svojim knjigovođom.", style="Help.TLabel", wraplength=300).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 5))
        add_combo(right, 6, 0, "Podrazumevana valuta", self.vars["default_currency"], list(SUPPORTED_CURRENCIES), width=14)
        self.vat_regime_combo = add_combo(right, 7, 0, "PDV režim", self.vars["vat_regime"], list(VAT_REGIME_LABELS.values()), width=34)
        self.vat_regime_combo.bind("<<ComboboxSelected>>", self._apply_vat_regime)
        add_combo(right, 8, 0, "E-faktura tok", self.vars["einvoice_route"], list(EINVOICE_ROUTE_LABELS.values()), width=34)
        ttk.Label(right, text="Automatski tok vezuje e-fakturu za državu firme. Ručni izbor ne menja poreska pravila niti omogućava pogrešan državni API.", style="Help.TLabel", wraplength=300).grid(row=9, column=0, columnspan=2, sticky="w", pady=(0, 5))
        add_field(right, 10, 0, "PDV stopa", self.vars["default_vat_rate"], width=14)
        add_field(right, 11, 0, "Rok plaćanja (dani)", self.vars["payment_term_days"], width=14)
        add_field(right, 12, 0, "Mesto izdavanja", self.vars["issue_place"], width=29)
        add_combo(right, 13, 0, "Jezik programa", self.vars["ui_language"], list(UI_LANGUAGE_LABELS.values()), width=18)

        access = ttk.LabelFrame(outer, text="Pristup aplikaciji", padding=10)
        access.grid(row=details_row + 1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        access.columnconfigure(1, weight=1)
        access.columnconfigure(3, weight=1)
        add_field(access, 0, 0, "E-mail za prijavu", self.vars["login_email"], width=30)
        add_field(access, 0, 2, "PIN (najmanje 4 cifre)", self.vars["login_pin"], width=16, show="*")
        add_field(access, 1, 0, "Ponovite PIN", self.vars["login_pin_confirm"], width=16, show="*")
        ttk.Label(
            access,
            text="PIN se čuva kao hash i koristi se samo za lokalni pristup ovom računaru.",
            style="Help.TLabel",
            wraplength=680,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(shell, style="App.TFrame")
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Kasnije", command=self.postpone).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Sačuvaj profil", style="Primary.TButton", command=self.save).pack(side="left")

    def browse_logo(self) -> None:
        path = filedialog.askopenfilename(title="Izaberi logo", filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if path:
            self.vars["logo_path"].set(path)

    def _apply_country_vat_default(self, _event: tk.Event | None = None) -> None:
        country_code = country_code_from_option(self.vars["country_code"].get())
        self.vars["default_currency"].set(default_currency_for_country(country_code))
        if vat_regime_code_from_label(self.vars["vat_regime"].get()) == "standard":
            self.vars["default_vat_rate"].set(f"{default_vat_rate_for_country(country_code):.2f}")

    def _apply_vat_regime(self, _event: tk.Event | None = None) -> None:
        if vat_regime_code_from_label(self.vars["vat_regime"].get()) == "standard":
            self._apply_country_vat_default()
        else:
            self.vars["default_vat_rate"].set("0.00")

    def save(self) -> None:
        if not self.vars["name"].get().strip():
            messagebox.showerror("Registracija firme", "Unesite naziv firme.")
            return
        if not self.vars["eik"].get().strip():
            messagebox.showerror("Registracija firme", "Unesite EIK / BULSTAT firme.")
            return
        pin = self.vars["login_pin"].get()
        pin_confirm = self.vars["login_pin_confirm"].get()
        requires_login_setup = self.from_access or not self.app.db.company_has_local_login()
        if requires_login_setup and not pin:
            messagebox.showerror("Registracija firme", "Postavite PIN za prijavu.")
            return
        if pin and pin != pin_confirm:
            messagebox.showerror("Registracija firme", "PIN i ponovljeni PIN se ne podudaraju.")
            return
        try:
            payload = dict(self.company)
            payload.update({
                key: var.get().strip()
                for key, var in self.vars.items()
                if key not in {"login_pin", "login_pin_confirm"}
            })
            payload["default_vat_rate"] = float(payload["default_vat_rate"] or DEFAULT_VAT_RATE)
            payload["payment_term_days"] = int(payload["payment_term_days"] or DEFAULT_PAYMENT_TERM_DAYS)
            payload["ui_language"] = language_code_from_label(payload["ui_language"])
            payload["country_code"] = country_code_from_option(payload["country_code"])
            payload["business_profile"] = business_profile_code_from_label(payload["business_profile"])
            payload["vat_regime"] = vat_regime_code_from_label(payload["vat_regime"])
            payload["einvoice_route"] = einvoice_route_code_from_label(payload["einvoice_route"])
            payload["smtp_from_name"] = str(payload.get("smtp_from_name") or payload["name"]).strip()
            payload["onboarding_completed"] = 1
        except ValueError as exc:
            messagebox.showerror("Registracija firme", f"Proverite unete podatke: {exc}")
            return
        trial_was_not_started = str(self.subscription.get("status") or "not_started") == "not_started"
        online_company = str(payload.get("name") or "").strip()
        online_email = str(payload.get("email") or payload.get("login_email") or "").strip()
        self.app.db.save_company(payload)
        self.app.company = self.app.db.get_company()
        if pin:
            try:
                self.app.db.set_company_login(self.vars["login_email"].get(), pin)
            except ValueError as exc:
                messagebox.showerror("Registracija firme", str(exc))
                return
        subscription = self.app.db.start_trial_if_needed()
        self.app.apply_language(payload["ui_language"], persist=False)
        self.app.refresh_subscription_status_indicator()
        self.destroy()
        if self.from_access:
            self.app.activate_workspace()
        if subscription.get("status") == "trial":
            messagebox.showinfo(
                "Sačuvano",
                f"Profil firme je sačuvan. Probni period je počeo odmah i traje 7 dana bez kartice. Preostalo dana: {subscription.get('days_remaining', 7)}.",
            )
            if trial_was_not_started and "@" in online_email:
                # New users are taken directly to the in-app e-mail step. No
                # browser is opened and blank installations never inherit data.
                self.app.after(180, lambda: self.app.open_online_activation(
                    prefill_company=online_company,
                    prefill_email=online_email,
                ))
        else:
            messagebox.showinfo("Sačuvano", "Profil firme je sačuvan i koristiće se na novim fakturama.")

    def postpone(self) -> None:
        if self.from_access:
            self.destroy()
            return
        payload = self.app.db.get_company()
        payload["onboarding_completed"] = 1
        self.app.db.save_company(payload)
        self.destroy()


class LocalLoginDialog(tk.Toplevel):
    """Unlock the current local company profile before building the workspace."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        company = app.db.get_company()
        self.title("Prijava")
        self.configure(background=BG)
        self.resizable(False, False)
        self.email_var = tk.StringVar(value=str(company.get("login_email") or ""))
        self.pin_var = tk.StringVar()
        self.status_var = tk.StringVar()

        outer = ttk.Frame(self, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="Prijava", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text="Prijavite se da otvorite poslovne podatke ove firme.",
            style="Help.TLabel",
            wraplength=410,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))
        email_entry = add_field(outer, 2, 0, "E-mail za prijavu", self.email_var, width=32)
        pin_entry = add_field(outer, 3, 0, "PIN", self.pin_var, width=18, show="*")
        ttk.Label(outer, textvariable=self.status_var, foreground="#B42318", background=BG, wraplength=410).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Prijavi se", style="Primary.TButton", command=self.login).pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")
        self.transient(master.winfo_toplevel())
        self.grab_set()
        center_window(self, 500, 310)
        localize_widget_tree(self, active_ui_language())
        pin_entry.focus_set() if self.email_var.get() else email_entry.focus_set()
        self.bind("<Return>", lambda event: self.login())
        self.bind("<Escape>", lambda event: self.destroy())

    def login(self) -> None:
        if not self.app.db.verify_company_login(self.email_var.get(), self.pin_var.get()):
            self.status_var.set("E-mail ili PIN nisu tačni.")
            self.pin_var.set("")
            return
        self.destroy()
        self.app.activate_workspace()


class CustomersTab(ttk.Frame):
    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master, style="App.TFrame")
        self.app = app
        self.selected_id: int | None = None
        self._search_refresh_job: str | None = None
        self.search_var = tk.StringVar()
        self.form_entries: list[ttk.Entry] = []
        self.vars = {
            "name": tk.StringVar(),
            "eik": tk.StringVar(),
            "vat_number": tk.StringVar(),
            "address": tk.StringVar(),
            "contact_person": tk.StringVar(),
            "phone": tk.StringVar(),
            "email": tk.StringVar(),
            "payment_term_days": tk.StringVar(value=str(DEFAULT_PAYMENT_TERM_DAYS)),
            "note": tk.StringVar(),
        }
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        bar = ttk.Frame(outer, style="App.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        filters = ttk.Frame(bar, style="App.TFrame")
        filters.pack(fill="x")
        ttk.Label(filters, text="Pretraga").pack(side="left")
        self.search_entry = ttk.Entry(filters, textvariable=self.search_var, width=32, style="Modern.TEntry")
        self.search_entry.pack(side="left", padx=8)
        ttk.Button(filters, text="Traži", style="Primary.TButton", command=self.refresh).pack(side="left")
        actions = ttk.Frame(bar, style="App.TFrame")
        actions.pack(anchor="e", pady=(6, 0))
        ttk.Button(actions, text="Novi", command=self.clear_form).pack(side="left", padx=3)
        paste_button = ttk.Button(actions, text="Nalepi kupca", command=self.paste_customer_from_clipboard)
        paste_button.pack(side="left", padx=3)
        add_tooltip(paste_button, "Nalepi jedan red iz Excela u sva polja kupca. Prepoznaje zaglavlja ili redosled polja u formi.")
        ttk.Button(actions, text="Sačuvaj", style="Primary.TButton", command=self.save).pack(side="left", padx=3)
        ttk.Button(actions, text="Sačuvaj i novi", command=self.save_and_new).pack(side="left", padx=3)
        ttk.Button(actions, text="Obriši", command=self.delete_selected).pack(side="left", padx=3)

        body = ttk.PanedWindow(outer, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew")

        list_frame = ttk.Frame(body, style="App.TFrame")
        body.add(list_frame, weight=2)
        form_frame = ttk.Frame(body, style="App.TFrame")
        body.add(form_frame, weight=3)

        cols = ("name", "eik", "vat", "address", "contact", "phone", "email", "term")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings")
        setup_treeview_tree(self.tree)
        headings = {
            "name": ("Naziv", 180, "w"),
            "eik": ("EIK", 95, "w"),
            "vat": ("PDV", 120, "w"),
            "address": ("Adresa", 220, "w"),
            "contact": ("Lice", 130, "w"),
            "phone": ("Telefon", 120, "w"),
            "email": ("E-mail", 160, "w"),
            "term": ("Rok", 60, "e"),
        }
        for key, (title, width, anchor) in headings.items():
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.on_select())

        form_frame.columnconfigure(1, weight=1)
        ttk.Label(
            form_frame,
            text="Ručni unos kupca: sačuvani podaci se zatim automatski prepisuju na novu fakturu.",
            style="Help.TLabel",
            wraplength=430,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=(0, 14), pady=(0, 10))
        row = 1
        for key, label in [
            ("name", "Naziv firme"),
            ("eik", "EIK / BULSTAT"),
            ("vat_number", "PDV broj"),
            ("address", "Adresa"),
            ("contact_person", "Odgovorno lice"),
            ("phone", "Telefon"),
            ("email", "E-mail"),
            ("payment_term_days", "Rok plaćanja (dani)"),
        ]:
            entry = add_field(form_frame, row, 0, label, self.vars[key], width=34)
            self.form_entries.append(entry)
            row += 1
        ttk.Label(form_frame, text="Napomena").grid(row=row, column=0, sticky="nw", padx=(0, 6), pady=3)
        self.note_text = tk.Text(form_frame, height=5, wrap="word", background="white", foreground=TEXT, insertbackground=TEXT, relief="solid", borderwidth=1, highlightthickness=1, highlightbackground=LINE)
        self.note_text.grid(row=row, column=1, sticky="nsew", padx=(0, 14), pady=3)
        self.note_text.bind("<Control-Return>", lambda e: self.save())

        self.search_entry.bind("<Return>", lambda e: (self.refresh(), "break")[1])
        self.search_var.trace_add("write", lambda *_: self._schedule_refresh())
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<Return>", lambda e: self.on_select())
        def focus_next(next_widget: tk.Widget) -> Callable[[tk.Event], str]:
            def handler(event: tk.Event) -> str:
                next_widget.focus_set()
                return "break"

            return handler

        for idx, widget in enumerate(self.form_entries[:-1]):
            widget.bind("<Return>", focus_next(self.form_entries[idx + 1]))
        if self.form_entries:
            self.form_entries[-1].bind("<Return>", focus_next(self.note_text))
        save_handler = lambda e: (self.save(), "break")[1]
        for widget in [*self.form_entries, self.note_text]:
            widget.bind("<Control-s>", save_handler)
            widget.bind("<Control-Return>", save_handler)
            widget.bind("<Control-n>", lambda e: (self.clear_form(), "break")[1])
            widget.bind("<Control-v>", self._smart_paste_handler)
            widget.bind("<Shift-Insert>", self._smart_paste_handler)
        self.search_entry.bind("<Control-n>", lambda e: (self.clear_form(), "break")[1])

    def _schedule_refresh(self) -> None:
        if self._search_refresh_job is not None:
            try:
                self.after_cancel(self._search_refresh_job)
            except tk.TclError:
                pass
        self._search_refresh_job = self.after(150, self._refresh_from_search)

    def _refresh_from_search(self) -> None:
        self._search_refresh_job = None
        self.refresh()

    def refresh(self) -> None:
        if self._search_refresh_job is not None:
            try:
                self.after_cancel(self._search_refresh_job)
            except tk.TclError:
                pass
            self._search_refresh_job = None
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.app.db.list_customers(self.search_var.get().strip()):
            idx = len(self.tree.get_children())
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["name"],
                    row["eik"],
                    row["vat_number"],
                    row["address"],
                    row["contact_person"],
                    row["phone"],
                    row["email"],
                    row["payment_term_days"],
                ),
                tags=(tree_row_tag(idx),),
            )

    def clear_form(self) -> None:
        self.selected_id = None
        for var in self.vars.values():
            var.set("")
        self.vars["payment_term_days"].set(str(DEFAULT_PAYMENT_TERM_DAYS))
        self.note_text.delete("1.0", "end")
        self.tree.selection_remove(self.tree.selection())
        if self.form_entries:
            self.form_entries[0].focus_set()

    def on_select(self, event: Any | None = None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        cid = int(sel[0])
        row = self.app.db.get_customer(cid)
        self.selected_id = cid
        for key, var in self.vars.items():
            value = row.get(key, "")
            if value is None:
                value = ""
            var.set(str(value))
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", row.get("note", ""))

    def save(self, show_message: bool = True) -> bool:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "unos ili izmena kupca",
            parent=self,
        ):
            return False
        try:
            payload = {k: v.get().strip() for k, v in self.vars.items()}
            payload["payment_term_days"] = int(payload["payment_term_days"] or DEFAULT_PAYMENT_TERM_DAYS)
            payload["note"] = self.note_text.get("1.0", "end").strip()
            if self.selected_id:
                payload["id"] = self.selected_id
        except ValueError as exc:
            messagebox.showerror("Greška", f"Nije moguće sačuvati kupca: {exc}")
            return False
        self.selected_id = self.app.db.save_customer(payload)
        self.refresh()
        self.app.refresh_all()
        if show_message:
            messagebox.showinfo("Sačuvano", "Kupac je sačuvan.")
        return True

    def save_and_new(self) -> None:
        if self.save(show_message=False):
            self.clear_form()

    def paste_customer_from_clipboard(self) -> bool:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Nalepi kupca", "Clipboard je prazan.")
            return False
        payload, header_map, source = entity_clipboard_payload_from_text(text, "customer")
        if not payload:
            return False
        for key, value in payload.items():
            if key == "note":
                self.note_text.delete("1.0", "end")
                self.note_text.insert("1.0", value)
            elif key in self.vars:
                self.vars[key].set(value)
        fields = ", ".join(ENTITY_CLIPBOARD_CONFIG["customer"]["labels"][key] for key in payload)
        messagebox.showinfo(
            "Kupac učitan",
            f"Popunjeno: {fields}.\n{entity_clipboard_mapping_summary('customer', header_map, source)}",
        )
        if self.form_entries:
            self.form_entries[0].focus_set()
        return True

    def _smart_paste_handler(self, event: tk.Event) -> str | None:
        return "break" if self.paste_customer_from_clipboard() else None

    def delete_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "arhiviranje kupca",
            parent=self,
        ):
            return
        if not self.selected_id:
            messagebox.showinfo("Kupac", "Izaberite kupca za arhiviranje.")
            return
        if not messagebox.askyesno("Potvrda", "Arhivirati izabranog kupca?"):
            return
        self.app.db.archive_customer(self.selected_id, True)
        self.clear_form()
        self.refresh()
        self.app.refresh_all()


class ProjectsTab(ttk.Frame):
    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master, style="App.TFrame")
        self.app = app
        self.selected_id: int | None = None
        self.search_var = tk.StringVar()
        self.customer_filter = tk.StringVar()
        self.form_widgets: list[tk.Widget] = []
        self.project_name_entry: ttk.Entry | None = None
        self.project_customer_combo: ttk.Combobox | None = None
        self.company_name_var = tk.StringVar()
        self.company_identity_var = tk.StringVar()
        self.company_contact_var = tk.StringVar()
        self.company_address_var = tk.StringVar()
        self.company_bank_var = tk.StringVar()
        self.company_settings_var = tk.StringVar()
        self.invoice_prefix_hint_var = tk.StringVar()
        self.vars = {
            "customer_id": tk.StringVar(),
            "name": tk.StringVar(),
            "invoice_prefix": tk.StringVar(),
            "site_address": tk.StringVar(),
            "contract_no": tk.StringVar(),
            "contract_net_amount": tk.StringVar(value="0"),
            "advance_percent": tk.StringVar(value="0"),
            "protocol_no": tk.StringVar(),
            "period_from": tk.StringVar(),
            "period_to": tk.StringVar(),
            "order_reference": tk.StringVar(),
        }
        self.vars["invoice_prefix"].trace_add("write", self._update_invoice_prefix_hint)
        self._build()
        self._update_invoice_prefix_hint()

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        outer.rowconfigure(2, weight=1)
        outer.columnconfigure(0, weight=1)

        company_frame = ttk.LabelFrame(outer, text="Podaci firme", padding=12)
        company_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        company_frame.columnconfigure(0, weight=1)
        company_header = ttk.Frame(company_frame, style="App.TFrame")
        company_header.grid(row=0, column=0, sticky="ew")
        company_header.columnconfigure(0, weight=1)
        ttk.Label(company_header, textvariable=self.company_name_var, style="CompanyName.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(company_header, text="Dopuni podatke firme", style="Primary.TButton", command=self.app.open_company_profile).grid(row=0, column=1, sticky="e")

        company_details = ttk.Frame(company_frame, style="App.TFrame")
        company_details.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        company_details.columnconfigure(0, weight=1)
        company_details.columnconfigure(1, weight=1)
        ttk.Label(company_details, textvariable=self.company_identity_var, style="CompanyInfo.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 18), pady=2)
        ttk.Label(company_details, textvariable=self.company_contact_var, style="CompanyInfo.TLabel").grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(company_details, textvariable=self.company_address_var, style="CompanyInfo.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 18), pady=2)
        ttk.Label(company_details, textvariable=self.company_bank_var, style="CompanyInfo.TLabel").grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(company_details, textvariable=self.company_settings_var, style="CompanyInfo.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=2)

        bar = ttk.Frame(outer, style="App.TFrame")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        filters = ttk.Frame(bar, style="App.TFrame")
        filters.pack(fill="x")
        ttk.Label(filters, text="Kupac", style="Field.TLabel").pack(side="left")
        self.customer_combo = ttk.Combobox(filters, textvariable=self.customer_filter, width=26, state="readonly", style="Modern.TCombobox")
        self.customer_combo.pack(side="left", padx=6)
        ttk.Label(filters, text="Pretraga", style="Field.TLabel").pack(side="left", padx=(16, 0))
        ttk.Entry(filters, textvariable=self.search_var, width=28, style="Modern.TEntry").pack(side="left", padx=6)
        ttk.Button(filters, text="Traži", style="Primary.TButton", command=self.refresh).pack(side="left")
        actions = ttk.Frame(bar, style="App.TFrame")
        actions.pack(anchor="e", pady=(6, 0))
        ttk.Button(actions, text="Novi projekat", command=self.clear_form).pack(side="left", padx=3)
        paste_button = ttk.Button(actions, text="Nalepi projekat", command=self.paste_project_from_clipboard)
        paste_button.pack(side="left", padx=3)
        add_tooltip(paste_button, "Nalepi jedan red iz Excela u polja projekta. Kupac ostaje izabran ručno, radi tačnog povezivanja baze.")
        ttk.Button(actions, text="Sačuvaj", style="Primary.TButton", command=self.save).pack(side="left", padx=3)
        ttk.Button(actions, text="Otvori projekat", command=self.open_financials).pack(side="left", padx=3)
        ttk.Button(actions, text="Dokumenti", command=self.open_documents).pack(side="left", padx=3)
        archive_button = ttk.Button(actions, text="Arhiviraj", command=self.delete_selected)
        archive_button.pack(side="left", padx=3)
        add_tooltip(
            archive_button,
            "Projekat i njegova istorija ostaju sačuvani; arhivirani projekat se samo uklanja iz aktivne liste.",
        )

        body = ttk.PanedWindow(outer, orient="horizontal")
        body.grid(row=2, column=0, sticky="nsew")
        list_frame = ttk.Frame(body, style="App.TFrame")
        body.add(list_frame, weight=2)
        # Project fields grow as accounting options are enabled. Keep them in
        # their own scrollable pane so the lower agreement/period fields never
        # disappear below the application window on compact screens.
        form_scroll = ScrollableFrame(body, style="App.TFrame")
        body.add(form_scroll, weight=3)
        form_frame = form_scroll.inner

        cols = ("customer", "name", "invoice_block", "address", "contract", "income", "expense", "profit")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings")
        setup_treeview_tree(self.tree)
        for key, title, width, anchor in [
            ("customer", "Kupac", 170, "w"),
            ("name", "Projekat", 180, "w"),
            ("invoice_block", "Sledeća faktura", 135, "w"),
            ("address", "Gradilište", 220, "w"),
            ("contract", "Ugovor bez PDV-a", 130, "e"),
            ("income", "Prihod bez PDV-a", 130, "e"),
            ("expense", "Trošak bez PDV-a", 130, "e"),
            ("profit", "Zarada", 130, "e"),
        ]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.open_financials())

        form_frame.columnconfigure(1, weight=1)
        ttk.Label(
            form_frame,
            text="Projekat je glavna jedinica rada. Kupac je opcioni podatak projekta; na svakoj fakturi birate konkretnog kupca.",
            style="Help.TLabel",
            wraplength=430,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=(0, 14), pady=(0, 10))
        row = 1
        self.project_customer_combo = add_combo(form_frame, row, 0, "Kupac (opciono)", self.vars["customer_id"], [], width=32)
        self.form_widgets.append(self.project_customer_combo)
        row += 1
        customer_actions = ttk.Frame(form_frame, style="App.TFrame")
        customer_actions.grid(row=row, column=1, sticky="w", padx=(0, 14), pady=(0, 6))
        ttk.Button(customer_actions, text="Dodaj kupca", command=self.create_customer_for_project).pack(side="left")
        row += 1
        for key, label in [
            ("name", "Naziv projekta"),
            ("invoice_prefix", "Oznaka bloka faktura"),
            ("site_address", "Adresa gradilišta"),
            ("contract_no", "Broj ugovora"),
            ("contract_net_amount", "Vrednost ugovora bez PDV-a"),
            ("advance_percent", "Avans po ugovoru (%)"),
            ("protocol_no", "Broj protokola / Akta 19"),
            ("period_from", "Period od (dd.mm.yyyy)"),
            ("period_to", "Period do (dd.mm.yyyy)"),
            ("order_reference", "Poređenja / referenca"),
        ]:
            field = add_field(form_frame, row, 0, label, self.vars[key], width=34)
            self.form_widgets.append(field)
            if key == "name":
                self.project_name_entry = field
            row += 1
            if key == "invoice_prefix":
                ttk.Label(
                    form_frame,
                    textvariable=self.invoice_prefix_hint_var,
                    style="Help.TLabel",
                    wraplength=410,
                ).grid(row=row, column=1, sticky="w", padx=(0, 14), pady=(0, 5))
                row += 1
        for widget in self.form_widgets:
            widget.bind("<Control-v>", self._smart_paste_handler)
            widget.bind("<Shift-Insert>", self._smart_paste_handler)

    def refresh(self) -> None:
        company = self.app.company
        blank = "-"
        self.company_name_var.set(str(company.get("name") or tr("Dopunite podatke firme")))
        self.company_identity_var.set(
            f"EIK / BULSTAT: {company.get('eik') or blank}    |    {tr('PDV')}: {company.get('vat_number') or blank}"
        )
        self.company_contact_var.set(
            f"{tr('Telefon')}: {company.get('phone') or blank}    |    {tr('E-mail')}: {company.get('email') or blank}"
        )
        self.company_address_var.set(
            f"{tr('Adresa')}: {company.get('address') or blank}    |    {tr('Direktor')}: {company.get('director_name') or blank}"
        )
        self.company_bank_var.set(
            f"{tr('Banka')}: {company.get('bank_name') or blank}    |    IBAN: {company.get('iban') or blank}    |    BIC: {company.get('bic') or blank}"
        )
        vat_rate = float(company.get("default_vat_rate") or DEFAULT_VAT_RATE) * 100
        country_text = country_option_label(company.get("country_code") or "BG").split(" - ", 1)[-1]
        self.company_settings_var.set(
            f"{tr('Država')}: {country_text}    |    {tr('Valuta')}: {company.get('default_currency') or DEFAULT_CURRENCY}    |    {tr('PDV stopa')}: {vat_rate:g}%    |    {tr('Rok plaćanja')}: {company.get('payment_term_days') or DEFAULT_PAYMENT_TERM_DAYS} {tr('dana')}"
        )
        customers = self.app.db.list_customers()
        customer_map = {"": ""}
        for row in customers:
            customer_map[f'{row["name"]} [{row["id"]}]'] = str(row["id"])
        customer_values = list(customer_map.keys())
        self.customer_combo["values"] = customer_values
        if self.project_customer_combo is not None:
            self.project_customer_combo["values"] = customer_values
        if self.customer_filter.get() not in customer_values:
            self.customer_filter.set("")

        for item in self.tree.get_children():
            self.tree.delete(item)
        customer_lookup = {str(row["id"]): row["name"] for row in customers}
        currency = self.app.company.get("default_currency") or DEFAULT_CURRENCY
        for row in self.app.db.list_project_financial_overview():
            if self.customer_filter.get():
                filter_id = customer_map.get(self.customer_filter.get(), "")
                if filter_id and str(row.get("customer_id") or "") != filter_id:
                    continue
            if self.search_var.get().strip():
                search = self.search_var.get().strip().lower()
                blob = " ".join(str(row.get(k, "")) for k in ["name", "site_address", "contract_no", "protocol_no", "order_reference"]).lower()
                if search not in blob:
                    continue
            financials = row.get("financials", {})
            try:
                invoice_block = project_invoice_number(
                    row.get("invoice_prefix"),
                    row.get("next_invoice_number") or 1,
                )
            except ValueError:
                invoice_block = ""
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row.get("customer_name") or customer_lookup.get(str(row.get("customer_id") or ""), ""),
                    row["name"],
                    invoice_block,
                    row["site_address"],
                    fmt_money(row.get("contract_net_amount", 0), currency),
                    fmt_money(financials.get("income_net", 0), currency),
                    fmt_money(financials.get("expense_net", 0), currency),
                    fmt_money(financials.get("profit_net", 0), currency),
                ),
                tags=(tree_row_tag(len(self.tree.get_children())),),
            )

    def clear_form(self) -> None:
        self.selected_id = None
        for var in self.vars.values():
            var.set("")
        self.tree.selection_remove(self.tree.selection())
        self._update_invoice_prefix_hint()

    def _update_invoice_prefix_hint(self, *_args: Any) -> None:
        prefix = self.vars["invoice_prefix"].get().strip()
        if not prefix:
            self.invoice_prefix_hint_var.set(
                tr("Ako ostavite prazno, program dodeljuje prvi slobodan blok. Primer: 1 -> 1000000001.")
            )
            return
        try:
            number = (
                self.app.db.preview_project_invoice_number(self.selected_id)
                if self.selected_id
                else project_invoice_number(prefix, 1)
            )
        except (ValueError, TypeError):
            self.invoice_prefix_hint_var.set("Unesite pozitivan broj, na primer 1 ili 2.")
            return
        self.invoice_prefix_hint_var.set(
            tr("Sledeća faktura ovog projekta biće: {number}. Prefiks se zaključava nakon prve fakture.").format(number=number)
        )

    def start_new_project(self) -> None:
        self.clear_form()
        if self.project_name_entry is not None:
            self.project_name_entry.focus_set()

    def create_customer_for_project(self) -> None:
        """Add a buyer without making the user leave the current project form."""
        fields = [
            ("name", "Naziv firme", "entry", ""),
            ("eik", "EIK / BULSTAT", "entry", ""),
            ("vat_number", "PDV broj", "entry", ""),
            ("address", "Adresa", "entry", ""),
            ("contact_person", "Odgovorno lice", "entry", ""),
            ("phone", "Telefon", "entry", ""),
            ("email", "E-mail", "entry", ""),
            ("payment_term_days", "Rok plaćanja (dani)", "entry", str(DEFAULT_PAYMENT_TERM_DAYS)),
            ("note", "Napomena", "text", ""),
        ]

        def on_save(payload: dict[str, Any]) -> bool:
            if not payload.get("name", "").strip():
                messagebox.showerror("Kupac", "Unesite naziv firme kupca.")
                return False
            try:
                payload["payment_term_days"] = int(payload.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)
            except ValueError:
                messagebox.showerror("Kupac", "Rok plaćanja mora biti broj dana.")
                return False
            customer_id = self.app.db.save_customer(payload)
            customer = self.app.db.get_customer(customer_id)
            self.refresh()
            self.app.customers_tab.refresh()
            self.vars["customer_id"].set(f'{customer["name"]} [{customer_id}]')
            if self.project_customer_combo is not None:
                self.project_customer_combo.focus_set()
            return True

        EntityLineDialog(self, "Novi kupac", fields, on_save)

    def on_select(self, event: Any | None = None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        row = self.app.db.get_project(pid)
        self.selected_id = pid
        customers = {str(c["id"]): c["name"] for c in self.app.db.list_customers()}
        customer_id = row.get("customer_id")
        self.vars["customer_id"].set(
            f'{customers.get(str(customer_id), "")} [{customer_id}]' if customer_id else ""
        )
        for key, var in self.vars.items():
            if key == "customer_id":
                continue
            value = row.get(key, "")
            if value is None:
                value = ""
            var.set(display_date(value) if key.startswith("period_") else str(value))

    def save(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager"},
            "dodavanje ili izmena projekta",
            parent=self,
        ):
            return
        is_new_project = self.selected_id is None
        customers = {f'{c["name"]} [{c["id"]}]': c["id"] for c in self.app.db.list_customers()}
        customer_id = customers.get(self.vars["customer_id"].get(), None)
        try:
            contract_raw = self.vars["contract_net_amount"].get().strip()
            advance_raw = self.vars["advance_percent"].get().strip()
            contract_net_amount = parse_clipboard_number(contract_raw)
            advance_percent = parse_clipboard_number(advance_raw)
            if contract_raw and contract_net_amount is None:
                raise ValueError("Vrednost ugovora bez PDV-a mora biti broj.")
            if advance_raw and advance_percent is None:
                raise ValueError("Procenat avansa mora biti broj.")
            payload = {
                "customer_id": customer_id,
                "name": self.vars["name"].get().strip(),
                "site_address": self.vars["site_address"].get().strip(),
                "contract_no": self.vars["contract_no"].get().strip(),
                "contract_net_amount": contract_net_amount or 0,
                "advance_percent": advance_percent or 0,
                "protocol_no": self.vars["protocol_no"].get().strip(),
                "period_from": self.vars["period_from"].get().strip(),
                "period_to": self.vars["period_to"].get().strip(),
                "order_reference": self.vars["order_reference"].get().strip(),
                "invoice_prefix": self.vars["invoice_prefix"].get().strip(),
            }
            if self.selected_id:
                payload["id"] = self.selected_id
        except Exception as exc:
            messagebox.showerror("Greška", f"Nije moguće sačuvati projekat: {exc}")
            return
        self.selected_id = self.app.db.save_project(payload)
        saved_project = self.app.db.get_project(self.selected_id)
        self.vars["invoice_prefix"].set(str(saved_project.get("invoice_prefix") or ""))
        self._update_invoice_prefix_hint()
        self.refresh()
        self.app.refresh_all()
        if is_new_project:
            self.open_financials()
        else:
            messagebox.showinfo("Sačuvano", "Projekat je sačuvan.")

    def open_financials(self) -> None:
        if not self.selected_id:
            messagebox.showinfo("Knjigovodstvo projekta", "Izaberite projekat iz liste, pa otvorite njegovo knjigovodstvo.")
            return
        self.app.open_project_finance(self.selected_id, on_changed=self.refresh)

    def open_documents(self) -> None:
        if not self.selected_id:
            messagebox.showinfo("Dokumenti projekta", "Izaberite projekat iz liste, pa otvorite njegove dokumente.")
            return
        ProjectArchiveDialog(self, self.app, self.selected_id)

    def paste_project_from_clipboard(self) -> bool:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Nalepi projekat", "Clipboard je prazan.")
            return False
        payload, header_map, source = entity_clipboard_payload_from_text(text, "project")
        if not payload:
            return False
        for key, value in payload.items():
            if key in self.vars and key != "customer_id":
                self.vars[key].set(value)
        fields = ", ".join(ENTITY_CLIPBOARD_CONFIG["project"]["labels"][key] for key in payload)
        messagebox.showinfo(
            "Projekat učitan",
            f"Popunjeno: {fields}.\nKupca izaberite ručno.\n{entity_clipboard_mapping_summary('project', header_map, source)}",
        )
        if len(self.form_widgets) > 1:
            self.form_widgets[1].focus_set()
        return True

    def _smart_paste_handler(self, event: tk.Event) -> str | None:
        return "break" if self.paste_project_from_clipboard() else None

    def delete_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager"},
            "arhiviranje projekta",
            parent=self,
        ):
            return
        if not self.selected_id:
            messagebox.showinfo("Projekat", "Izaberite projekat za arhiviranje.")
            return
        if not messagebox.askyesno("Potvrda", "Arhivirati izabrani projekat?"):
            return
        self.app.db.archive_project(self.selected_id, True)
        self.clear_form()
        self.refresh()
        self.app.refresh_all()


class ProjectDocumentDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        app: MainApp,
        project_id: int,
        *,
        document_type: str = "input",
        document_id: int | None = None,
        on_saved: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.project_id = project_id
        self.document_id = document_id
        self.on_saved = on_saved
        self.title("Projektni račun / trošak")
        self.configure(background=BG)
        self.resizable(True, True)
        self.type_map = {
            tr("Ulazni račun / trošak"): "input",
            tr("Izlazni račun"): "output",
        }
        self.type_var = tk.StringVar(value=tr("Ulazni račun / trošak" if document_type == "input" else "Izlazni račun"))
        default_group = PROJECT_COST_GROUPS[-1] if document_type == "input" else PROJECT_INCOME_GROUPS[0]
        self.group_var = tk.StringVar(value=tr(default_group))
        self.date_var = tk.StringVar(value=date.today().strftime("%d.%m.%Y"))
        self.number_var = tk.StringVar()
        self.partner_var = tk.StringVar()
        self.description_var = tk.StringVar()
        self.net_var = tk.StringVar(value="0")
        self.vat_var = tk.StringVar(value="20")
        self.gross_var = tk.StringVar(value="0,00 EUR")
        self.currency_var = tk.StringVar(value=DEFAULT_CURRENCY)
        self.note_var = tk.StringVar()
        self.source_pdf_path: Path | None = None
        self.ocr_partner_name = ""
        self.non_eur_source_currency = ""
        self.import_status_var = tk.StringVar(value=tr("PDF račun još nije izabran."))
        if document_id:
            self._load_document()
        self._build()
        self._sync_type_fields()
        self._refresh_total()
        self.bind("<Return>", lambda event: self.save())
        self.bind("<Escape>", lambda event: self.destroy())
        self.bind("<Control-o>", lambda event: self.import_pdf())
        maximize_large_window(self, minimum_width=720, minimum_height=690)
        localize_widget_tree(self, self.app.ui_language)

    def _load_document(self) -> None:
        row = self.db.get_project_document(self.document_id or 0)
        if not row:
            return
        self.type_var.set(tr("Izlazni račun" if row.get("document_type") == "output" else "Ulazni račun / trošak"))
        self.group_var.set(tr(row.get("cost_group") or PROJECT_COST_GROUPS[-1]))
        self.date_var.set(display_date(row.get("document_date")))
        self.number_var.set(row.get("document_no") or "")
        self.partner_var.set(row.get("partner_name") or "")
        self.description_var.set(row.get("description") or "")
        self.net_var.set(format_clipboard_number(row.get("net_amount")) or "0")
        self.vat_var.set(format_clipboard_number(float(row.get("vat_rate") or 0) * 100) or "0")
        self.currency_var.set(row.get("currency") or DEFAULT_CURRENCY)
        self.note_var.set(row.get("note") or "")

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        project = self.db.get_project(self.project_id)
        ttk.Label(outer, text="Ulazni i izlazni račun projekta", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(outer, text=project.get("name") or "", style="Help.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))

        self.pdf_frame = ttk.LabelFrame(outer, text="Uvoz PDF računa", padding=10)
        self.pdf_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.pdf_frame.columnconfigure(1, weight=1)
        self.import_pdf_button = ttk.Button(
            self.pdf_frame,
            text="Uvezi podatke iz PDF računa",
            style="Primary.TButton",
            command=self.import_pdf,
        )
        self.import_pdf_button.grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Label(self.pdf_frame, textvariable=self.import_status_var, style="Help.TLabel", wraplength=520).grid(row=0, column=1, sticky="w")
        ttk.Label(self.pdf_frame, text="Pre čuvanja proverite prepoznate podatke. PDF se kopira u projekat.", style="Help.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(outer, text="Tip", style="Field.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 12), pady=5)
        self.type_combo = ttk.Combobox(outer, textvariable=self.type_var, values=list(self.type_map), state="readonly", style="Modern.TCombobox")
        self.type_combo.grid(row=3, column=1, sticky="ew", pady=5)
        self.type_combo.bind("<<ComboboxSelected>>", lambda event: self._sync_type_fields())

        self.group_label = ttk.Label(outer, text="Grupa troška", style="Field.TLabel")
        self.group_label.grid(row=4, column=0, sticky="w", padx=(0, 12), pady=5)
        self.group_combo = ttk.Combobox(outer, textvariable=self.group_var, values=PROJECT_COST_GROUPS, state="readonly", style="Modern.TCombobox")
        self.group_combo.grid(row=4, column=1, sticky="ew", pady=5)

        for row, label, variable in [
            (5, "Datum", self.date_var),
            (6, "Broj računa / dokumenta", self.number_var),
            (7, "Dobavljač / kupac", self.partner_var),
            (8, "Opis", self.description_var),
        ]:
            ttk.Label(outer, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            ttk.Entry(outer, textvariable=variable, style="Modern.TEntry").grid(row=row, column=1, sticky="ew", pady=5)

        amount_frame = ttk.Frame(outer, style="App.TFrame")
        amount_frame.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        for idx in range(3):
            amount_frame.columnconfigure(idx, weight=1)
        ttk.Label(amount_frame, text="Iznos bez PDV-a", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(amount_frame, text="PDV %", style="Field.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(amount_frame, text="Ukupno sa PDV-om", style="Field.TLabel").grid(row=0, column=2, sticky="w", padx=(10, 0))
        net_entry = ttk.Entry(amount_frame, textvariable=self.net_var, style="Modern.TEntry")
        net_entry.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        vat_entry = ttk.Entry(amount_frame, textvariable=self.vat_var, style="Modern.TEntry")
        vat_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(3, 0))
        ttk.Label(amount_frame, textvariable=self.gross_var, style="TotalValue.TLabel", padding=(10, 7)).grid(row=1, column=2, sticky="ew", padx=(10, 0), pady=(3, 0))
        for widget in (net_entry, vat_entry):
            widget.bind("<KeyRelease>", lambda event: self._refresh_total())
            widget.bind("<FocusOut>", lambda event: self._refresh_total())

        ttk.Label(outer, text="Valuta", style="Field.TLabel").grid(row=10, column=0, sticky="w", padx=(0, 12), pady=5)
        self.currency_combo = ttk.Combobox(
            outer,
            textvariable=self.currency_var,
            values=[DEFAULT_CURRENCY],
            state="disabled",
            style="Modern.TCombobox",
        )
        self.currency_combo.grid(row=10, column=1, sticky="ew", pady=5)
        ttk.Label(outer, text="Napomena", style="Field.TLabel").grid(row=11, column=0, sticky="w", padx=(0, 12), pady=5)
        ttk.Entry(outer, textvariable=self.note_var, style="Modern.TEntry").grid(row=11, column=1, sticky="ew", pady=5)

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Sačuvaj stavku", style="Primary.TButton", command=self.save).pack(side="left")
        self.save_and_payable_button = ttk.Button(
            buttons,
            text="Sačuvaj i kreiraj obavezu",
            command=lambda: self.save(create_payable=True),
        )
        self.save_and_payable_button.pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")

    def _sync_type_fields(self) -> None:
        is_output = self.type_map.get(self.type_var.get()) == "output"
        if is_output:
            valid_groups = PROJECT_INCOME_GROUPS
            fallback_group = "Ostali prihodi"
            self.group_label.configure(text=tr("Grupa prihoda"))
            self.pdf_frame.configure(text=tr("Uvoz izlaznog PDF računa"))
            self.import_pdf_button.configure(text=tr("Uvezi izlazni PDF račun"), state="normal")
            self.save_and_payable_button.configure(state="disabled")
        else:
            valid_groups = PROJECT_COST_GROUPS
            fallback_group = "Ostali troškovi"
            self.group_label.configure(text=tr("Grupa troška"))
            self.pdf_frame.configure(text=tr("Uvoz ulaznog PDF računa"))
            self.import_pdf_button.configure(text=tr("Uvezi ulazni PDF račun"), state="normal")
            self.save_and_payable_button.configure(state="normal")
        selected_group = canonical_ui_text(self.group_var.get(), self.app.ui_language)
        if selected_group not in valid_groups:
            selected_group = fallback_group
        self.group_var.set(tr(selected_group))
        self.group_combo.configure(values=[tr(group) for group in valid_groups], state="readonly")

    def import_pdf(self) -> None:
        is_output = self.type_map.get(self.type_var.get()) == "output"
        source = filedialog.askopenfilename(
            parent=self,
            title=tr("Izaberite izlazni PDF račun" if is_output else "Izaberite ulazni PDF račun"),
            filetypes=[("PDF račun", "*.pdf"), ("Svi fajlovi", "*.*")],
        )
        if not source:
            return
        try:
            fields = extract_invoice_fields_from_pdf(source, document_type="output" if is_output else "input")
        except PdfInvoiceReadError as exc:
            messagebox.showerror("Uvoz PDF računa", str(exc), parent=self)
            return
        except Exception as exc:
            messagebox.showerror("Uvoz PDF računa", f"PDF nije moguće obraditi:\n{exc}", parent=self)
            return

        self.source_pdf_path = Path(source)
        extracted_partner = str(fields.get("partner_name") or "").strip()
        self.ocr_partner_name = extracted_partner
        remembered = self.db.get_pdf_partner_mapping(extracted_partner)
        matched_partner = str(remembered.get("partner_name") or "").strip()
        if remembered:
            fields["partner_name"] = matched_partner
            if remembered.get("cost_group"):
                fields["cost_group"] = remembered["cost_group"]
            if remembered.get("vat_rate") is not None:
                fields["vat_rate_percent"] = float(remembered["vat_rate"]) * 100
            fields["matched_partner"] = True
        else:
            matched_partner = match_known_partner(
                extracted_partner,
                [str(customer.get("name") or "") for customer in self.app.db.list_customers(include_archived=True)],
            )
            if matched_partner:
                fields["partner_name"] = matched_partner
                fields["matched_partner"] = True
        self._sync_type_fields()
        if fields.get("cost_group"):
            self.group_var.set(tr(str(fields["cost_group"])))
            self._sync_type_fields()
        if fields.get("document_date") and parse_date(fields["document_date"]):
            self.date_var.set(parse_date(fields["document_date"]).strftime("%d.%m.%Y"))
        if fields.get("document_no"):
            self.number_var.set(str(fields["document_no"]))
        if fields.get("partner_name"):
            self.partner_var.set(str(fields["partner_name"]))
        if fields.get("description"):
            self.description_var.set(str(fields["description"]))
        if fields.get("net_amount") is not None:
            self.net_var.set(format_clipboard_number(fields["net_amount"]))
        if fields.get("vat_rate_percent") is not None:
            self.vat_var.set(format_clipboard_number(fields["vat_rate_percent"]))
        detected_currency = str(fields.get("currency") or "").strip().upper()
        self.non_eur_source_currency = ""
        self.currency_var.set(DEFAULT_CURRENCY)
        if detected_currency and detected_currency not in {DEFAULT_CURRENCY, "€"}:
            self.non_eur_source_currency = detected_currency
            fields.setdefault("warnings", []).append(
                f"PDF je prepoznat kao {detected_currency}; OpsNest trenutno prihvata samo EUR"
            )
        pdf_note = f"PDF: {self.source_pdf_path.name}"
        if pdf_note not in self.note_var.get():
            self.note_var.set(f"{self.note_var.get().strip()} {pdf_note}".strip())
        self._refresh_total()
        partner_status = tr(" Partner je povezan sa bazom firmi.") if fields.get("matched_partner") else ""
        self.import_status_var.set(
            tr("Prepoznato ({method}): {filename}. Proverite podatke pre čuvanja.{partner_status}").format(
                method=fields.get("extraction_method") or "PDF",
                filename=self.source_pdf_path.name,
                partner_status=partner_status,
            )
        )
        warnings = fields.get("warnings") or []
        if warnings:
            messagebox.showwarning(
                "Provera PDF podataka",
                "Neka polja nisu sigurno prepoznata: " + ", ".join(warnings) + ".\n\n"
                "Dopunite ili ispravite polja pre čuvanja.",
                parent=self,
            )

    def _archive_imported_pdf(self, document_id: int) -> None:
        if not self.source_pdf_path:
            return
        source = self.source_pdf_path
        if not source.is_file():
            raise OSError("Izabrani PDF više nije dostupan.")
        document_date = parse_date(self.date_var.get()) or date.today()
        is_output = self.type_map.get(self.type_var.get()) == "output"
        number_part = safe_filename(self.number_var.get() or ("izlazni_racun" if is_output else "ulazni_racun"))
        source_part = safe_filename(source.stem)
        archive_dir = self.db.project_output_invoices_dir(self.project_id) if is_output else self.db.project_input_invoices_dir(self.project_id)
        destination = archive_dir / (
            f"{document_date.isoformat()}_{number_part}_{document_id}_{source_part}.pdf"
        )
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)

    def _refresh_total(self) -> None:
        net = parse_clipboard_number(self.net_var.get()) or 0
        rate = parse_clipboard_number(self.vat_var.get()) or 0
        if rate > 1:
            rate /= 100
        gross = net * (1 + max(rate, 0))
        self.gross_var.set(fmt_money(gross, self.currency_var.get() or DEFAULT_CURRENCY))

    def save(self, *, create_payable: bool = False) -> None:
        document_type = self.type_map.get(self.type_var.get(), "input")
        allowed_roles = {"owner", "administrator", "project_manager", "accountant"}
        action = "unos ili izmena izlaznog računa"
        if document_type == "input":
            allowed_roles.add("operator")
            action = "unos ili izmena ulaznog računa"
        if not self.app.require_team_permission(allowed_roles, action, parent=self):
            return
        net = parse_clipboard_number(self.net_var.get())
        vat = parse_clipboard_number(self.vat_var.get())
        if net is None or vat is None:
            messagebox.showerror("Projektni dokument", "Unesite ispravan iznos bez PDV-a i stopu PDV-a.")
            return
        if self.non_eur_source_currency:
            messagebox.showerror(
                "Projektni dokument",
                tr("Uvezeni PDF je u valuti {currency}. OpsNest trenutno čuva samo EUR dokumente.").format(
                    currency=self.non_eur_source_currency
                )
                + "\n\n"
                + tr("Izaberite račun izdat u eurima ili unesite preračunati EUR dokument ručno."),
            )
            return
        try:
            payload = {
                "id": self.document_id,
                "project_id": self.project_id,
                "document_type": self.type_map.get(self.type_var.get(), "input"),
                "cost_group": canonical_ui_text(self.group_var.get(), self.app.ui_language),
                "document_date": self.date_var.get(),
                "document_no": self.number_var.get(),
                "partner_name": self.partner_var.get(),
                "description": self.description_var.get(),
                "net_amount": net,
                "vat_rate": vat / 100 if vat > 1 else vat,
                "currency": self.currency_var.get(),
                "note": self.note_var.get(),
                "ocr_partner_name": self.ocr_partner_name,
            }
            document_id = self.db.save_project_document(payload)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Projektni dokument", f"Nije moguće sačuvati stavku:\n{exc}")
            return
        if self.source_pdf_path:
            try:
                self._archive_imported_pdf(document_id)
            except OSError as exc:
                messagebox.showwarning(
                    "PDF je ostao van projekta",
                    "Stavka kalkulacije je sačuvana, ali PDF nije moguće kopirati u projekat:\n"
                    f"{exc}",
                    parent=self,
                )
        if self.on_saved:
            self.on_saved()
        if create_payable:
            ProjectDocumentPayableDialog(self.app, self.app, document_id, on_saved=self.app.refresh_all)
        self.destroy()


class ProjectDocumentPayableDialog(tk.Toplevel):
    """Make an approved-to-pay supplier bill from one already reviewed project document."""

    def __init__(self, parent: tk.Widget, app: MainApp, document_id: int, *, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app, self.db, self.document_id, self.on_saved = app, app.db, int(document_id), on_saved
        self.document = self.db.get_project_document(self.document_id)
        self.vendor_ids: dict[str, int] = {}
        self.vendor_var = tk.StringVar()
        self.due_date_var = tk.StringVar()
        self.title("Kreiraj obavezu dobavljača")
        self.configure(background=BG)
        self._build()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        center_window(self, 700, 410)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        if not self.document or self.document.get("document_type") != "input":
            ttk.Label(outer, text="Ulazni dokument više nije dostupan.", style="Section.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Button(outer, text="Zatvori", command=self.destroy).grid(row=1, column=0, sticky="w", pady=(14, 0))
            return
        existing = self.db.vendor_bill_for_project_document(self.document_id)
        if existing:
            ttk.Label(outer, text="Obaveza je već kreirana za ovaj dokument.", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(outer, text=f"Obaveza #{existing.get('id')} — {existing.get('vendor_name') or ''}", style="Help.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 12))
            ttk.Button(outer, text="Zatvori", command=self.destroy).grid(row=2, column=0, sticky="w")
            return
        ttk.Label(outer, text="Pretvori dokument u obavezu za plaćanje", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text="Dokument ostaje dokaz troška projekta; obaveza služi za rok plaćanja i vezu sa bankovnim odlivom. Trošak se ne duplira u P&L-u.",
            style="Help.TLabel",
            wraplength=620,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 14))
        vendors = self.db.list_vendors()
        for vendor in vendors:
            label = f"{vendor.get('name')} [{vendor.get('id')}]"
            self.vendor_ids[label] = int(vendor["id"])
            if str(vendor.get("name") or "").strip().casefold() == str(self.document.get("partner_name") or "").strip().casefold():
                self.vendor_var.set(label)
        if not self.vendor_var.get() and self.vendor_ids:
            self.vendor_var.set(next(iter(self.vendor_ids)))
        due_default = (parse_date(self.document.get("document_date")) or date.today()) + timedelta(days=DEFAULT_PAYMENT_TERM_DAYS)
        self.due_date_var.set(due_default.isoformat())
        details = (
            ("Dokument", self.document.get("document_no") or f"#{self.document_id}"),
            ("Projekat", self.db.get_project(int(self.document.get("project_id") or 0)).get("name") or ""),
            ("Dobavljač", ""),
            ("Rok plaćanja", ""),
            ("Ukupno", fmt_money(self.document.get("gross_amount"), self.document.get("currency") or DEFAULT_CURRENCY)),
        )
        for row, (label, value) in enumerate(details, start=2):
            ttk.Label(outer, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            if label == "Dobavljač":
                ttk.Combobox(outer, textvariable=self.vendor_var, values=list(self.vendor_ids), state="readonly", style="Modern.TCombobox").grid(row=row, column=1, sticky="ew", pady=5)
            elif label == "Rok plaćanja":
                ttk.Entry(outer, textvariable=self.due_date_var, style="Modern.TEntry").grid(row=row, column=1, sticky="ew", pady=5)
            else:
                ttk.Label(outer, text=value, style="CardTitle.TLabel").grid(row=row, column=1, sticky="w", pady=5)
        if not self.vendor_ids:
            ttk.Label(outer, text="Najpre dodajte dobavljača u Finansije, pa ponovite ovu akciju.", style="Help.TLabel").grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Button(buttons, text="Kreiraj obavezu", style="Primary.TButton", command=self.save, state="normal" if self.vendor_ids else "disabled").pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")

    def save(self) -> None:
        vendor_id = self.vendor_ids.get(self.vendor_var.get())
        if not vendor_id:
            messagebox.showerror("Obaveza dobavljača", "Izaberite dobavljača.", parent=self)
            return
        approval_required = self.app.invoice_approval_enabled() and not self.app.is_owner_or_administrator()
        actor = self.app.active_team_member_name()
        try:
            bill_id = self.db.create_vendor_bill_from_project_document(
                self.document_id,
                vendor_id,
                self.due_date_var.get(),
                approval_status="pending" if approval_required else "approved",
                prepared_by_name=actor,
                approved_by_name="" if approval_required else actor,
            )
        except (ValueError, sqlite3.Error) as exc:
            messagebox.showerror("Obaveza dobavljača", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        messagebox.showinfo(
            "Obaveza dobavljača",
            f"Kreirana je obaveza #{bill_id}. "
            + ("Poslata je vlasniku na odobrenje pre plaćanja." if approval_required else "Sada je vidljiva u Finansijama i može se povezati sa odlivom banke."),
            parent=self,
        )
        self.destroy()


class ProjectArchiveDialog(tk.Toplevel):
    """In-app file browser for every document that belongs to one project."""

    def __init__(self, parent: tk.Widget, app: MainApp, project_id: int) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.project_id = project_id
        self.project = self.db.get_project(project_id)
        self.archive_root = self.db.project_archive_dir(project_id)
        self.paths: dict[str, Path] = {}
        self.title("Dokumenti projekta")
        self.configure(background=BG)
        self._build()
        maximize_large_window(self, minimum_width=800, minimum_height=520)
        localize_widget_tree(self, self.app.ui_language)
        self.refresh()
        self.bind("<Escape>", lambda event: self.destroy())

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=12)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(3, weight=1)
        outer.columnconfigure(0, weight=1)

        title = ttk.Frame(outer, style="App.TFrame")
        title.grid(row=0, column=0, sticky="ew")
        ttk.Label(title, text=self.project.get("name") or "Projekat", style="Section.TLabel").pack(side="left")
        ttk.Label(title, text="Dokumenti, fakture i prilozi organizovani u jednoj projektnoj arhivi.", style="Help.TLabel").pack(side="left", padx=10)
        ttk.Button(title, text="Zatvori", command=self.destroy).pack(side="right")

        toolbar = ttk.Frame(outer, style="App.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        ttk.Button(toolbar, text="Dodaj dokument", style="Primary.TButton", command=self.add_documents).pack(side="left")
        ttk.Button(toolbar, text="Otvori", command=self.open_selected).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Otvori folder", command=self.open_selected_folder).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Osveži", command=self.refresh).pack(side="right")

        cols = ("folder", "name", "type", "modified", "size")
        self.tree = ttk.Treeview(outer, columns=cols, show="headings")
        setup_treeview_tree(self.tree)
        for key, label, width, anchor in [
            ("folder", "Lokacija", 380, "w"),
            ("name", "Dokument", 310, "w"),
            ("type", "Tip", 105, "w"),
            ("modified", "Izmenjeno", 150, "w"),
            ("size", "Veličina", 100, "e"),
        ]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.grid(row=2, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda event: self.open_selected())
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=2, column=1, sticky="ns")

        ttk.Label(
            outer,
            text=f"Projektni folder: {self.archive_root}",
            style="Help.TLabel",
            wraplength=1040,
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))

    @staticmethod
    def _size_label(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    @staticmethod
    def _document_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return "PDF"
        if suffix in {".xlsx", ".xls", ".xlsm", ".csv"}:
            return "Excel"
        if suffix in {".doc", ".docx"}:
            return "Word"
        if suffix in {".jpg", ".jpeg", ".png"}:
            return "Slika"
        return suffix.lstrip(".").upper() or "Datoteka"

    def refresh(self) -> None:
        self.archive_root = self.db.project_archive_dir(self.project_id)
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.paths.clear()
        files = sorted(
            (path for path in self.archive_root.rglob("*") if path.is_file()),
            key=lambda path: (str(path.parent).lower(), path.name.lower()),
        )
        for index, path in enumerate(files, start=1):
            key = f"file:{index}"
            self.paths[key] = path
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
                size = self._size_label(path.stat().st_size)
            except OSError:
                modified, size = "", ""
            relative_parent = str(path.parent.relative_to(self.archive_root)) or "."
            self.tree.insert(
                "",
                "end",
                iid=key,
                values=(relative_parent, path.name, self._document_type(path), modified, size),
                tags=(tree_row_tag(index - 1),),
            )

    def _selected_path(self) -> Path | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self.paths.get(selection[0])

    def add_documents(self) -> None:
        paths = filedialog.askopenfilenames(title="Dodaj dokumente projektu")
        if not paths:
            return
        target_root = self.db.project_documents_dir(self.project_id)
        copied = 0
        for raw_path in paths:
            source = Path(raw_path)
            if not source.is_file():
                continue
            target = target_root / source.name
            index = 1
            while target.exists() and target.resolve() != source.resolve():
                target = target_root / f"{source.stem}_{index}{source.suffix}"
                index += 1
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
                copied += 1
        self.refresh()
        if copied:
            messagebox.showinfo("Dokumenti projekta", f"Dodato dokumenata: {copied}")

    def open_selected(self) -> None:
        path = self._selected_path()
        if not path:
            messagebox.showinfo("Dokumenti projekta", "Izaberite dokument iz pregleda.")
            return
        if not path.exists():
            messagebox.showwarning("Dokumenti projekta", "Datoteka više ne postoji na disku.")
            self.refresh()
            return
        open_path(path)

    def open_selected_folder(self) -> None:
        path = self._selected_path()
        open_path(path.parent if path else self.archive_root)


class ProjectBudgetDialog(tk.Toplevel):
    """Set an auditable project plan and compare it with current bookkeeping."""

    def __init__(self, parent: tk.Widget, app: MainApp, project_id: int, *, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.project_id = project_id
        self.on_saved = on_saved
        self.project = self.db.get_project(project_id)
        self.summary = self.db.project_financial_summary(project_id)
        budget = self.summary["budget"]
        self.vars = {
            "planned_income_net": tk.StringVar(value=format_clipboard_number(budget["planned_income_net"]) or "0"),
            "planned_rad_net": tk.StringVar(value=format_clipboard_number(budget["planned_rad_net"]) or "0"),
            "planned_material_net": tk.StringVar(value=format_clipboard_number(budget["planned_material_net"]) or "0"),
            "planned_plates_net": tk.StringVar(value=format_clipboard_number(budget["planned_plates_net"]) or "0"),
            "planned_other_costs_net": tk.StringVar(value=format_clipboard_number(budget["planned_other_costs_net"]) or "0"),
        }
        self.preview_vars: dict[str, tk.StringVar] = {}
        self.title("Budžet projekta")
        self.configure(background=BG)
        self.resizable(True, True)
        self._build()
        self._refresh_preview()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        center_window(self, 900, 590)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        header = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Plan projekta bez PDV-a", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(self.project.get("name") or "Projekat") + " | Budžet se poredi sa stvarnim fakturama i ulaznim računima.",
            style="Help.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        plan = ttk.LabelFrame(outer, text="Planirani iznosi bez PDV-a", padding=12)
        plan.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        plan.columnconfigure(1, weight=1)
        fields = [
            ("planned_income_net", "Planirani prihod"),
            ("planned_rad_net", "Budžet - Rad"),
            ("planned_material_net", "Budžet - Materijal"),
            ("planned_plates_net", "Budžet - Plate"),
            ("planned_other_costs_net", "Budžet - Ostali troškovi"),
        ]
        for row, (key, label) in enumerate(fields):
            entry = add_field(plan, row, 0, label, self.vars[key], width=20)
            ttk.Label(plan, text=self.app.company.get("default_currency") or DEFAULT_CURRENCY, style="Help.TLabel").grid(row=row, column=2, sticky="w", padx=(6, 0), pady=3)
            entry.bind("<KeyRelease>", lambda _event: self._refresh_preview())
            entry.bind("<FocusOut>", lambda _event: self._refresh_preview())

        comparison = ttk.LabelFrame(outer, text="Plan naspram stvarnog", padding=10)
        comparison.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        for col in range(4):
            comparison.columnconfigure(col, weight=1 if col else 2)
        for col, label in enumerate(("Stavka", "Plan", "Stvarno", "Odstupanje")):
            ttk.Label(comparison, text=label, style="CardTitle.TLabel").grid(row=0, column=col, sticky="w" if col == 0 else "e", padx=6, pady=(0, 6))
        for row, key, label in [
            (1, "income", "Prihod"),
            (2, "Rad", "Rad"),
            (3, "Materijal", "Materijal"),
            (4, "Plate", "Plate"),
            (5, "Ostali troškovi", "Ostali troškovi"),
            (6, "profit", "Zarada"),
        ]:
            ttk.Label(comparison, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=6, pady=3)
            for col, suffix in ((1, "plan"), (2, "actual"), (3, "variance")):
                var = tk.StringVar(value="0,00 EUR")
                self.preview_vars[f"{key}_{suffix}"] = var
                ttk.Label(comparison, textvariable=var, style="Help.TLabel").grid(row=row, column=col, sticky="e", padx=6, pady=3)
        ttk.Label(
            comparison,
            text="Kod troškova pozitivan iznos odstupanja znači da je potrošeno više od planiranog.",
            style="Help.TLabel",
        ).grid(row=7, column=0, columnspan=4, sticky="w", padx=6, pady=(8, 0))

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Sačuvaj budžet", style="Primary.TButton", command=self.save).pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")

    def _plan_values(self) -> dict[str, float]:
        values: dict[str, float] = {}
        for key, variable in self.vars.items():
            parsed = parse_clipboard_number(variable.get())
            values[key] = parsed if parsed is not None else 0.0
        return values

    def _refresh_preview(self) -> None:
        plan = self._plan_values()
        currency = self.app.company.get("default_currency") or DEFAULT_CURRENCY
        actual_by_group = {group: float(self.summary["cost_groups"][group]["net"]) for group in PROJECT_COST_GROUPS}
        planned_by_group = {
            "Rad": plan["planned_rad_net"],
            "Materijal": plan["planned_material_net"],
            "Plate": plan["planned_plates_net"],
            "Ostali troškovi": plan["planned_other_costs_net"],
        }
        actual_income = float(self.summary["income_net"])
        actual_profit = float(self.summary["profit_net"])
        self.preview_vars["income_plan"].set(fmt_money(plan["planned_income_net"], currency))
        self.preview_vars["income_actual"].set(fmt_money(actual_income, currency))
        self.preview_vars["income_variance"].set(fmt_money(actual_income - plan["planned_income_net"], currency))
        for group in PROJECT_COST_GROUPS:
            self.preview_vars[f"{group}_plan"].set(fmt_money(planned_by_group[group], currency))
            self.preview_vars[f"{group}_actual"].set(fmt_money(actual_by_group[group], currency))
            self.preview_vars[f"{group}_variance"].set(fmt_money(actual_by_group[group] - planned_by_group[group], currency))
        planned_profit = plan["planned_income_net"] - sum(planned_by_group.values())
        self.preview_vars["profit_plan"].set(fmt_money(planned_profit, currency))
        self.preview_vars["profit_actual"].set(fmt_money(actual_profit, currency))
        self.preview_vars["profit_variance"].set(fmt_money(actual_profit - planned_profit, currency))

    def save(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager"},
            "izmena budžeta projekta",
            parent=self,
        ):
            return
        payload = self._plan_values()
        if any(value < 0 for value in payload.values()):
            messagebox.showerror("Budžet projekta", "Budžetski iznosi ne mogu biti negativni.", parent=self)
            return
        try:
            self.db.save_project_budget(self.project_id, payload)
        except ValueError as exc:
            messagebox.showerror("Budžet projekta", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()


class ProjectVatEvidenceDialog(tk.Toplevel):
    """Small period selector for an accountant-ready project VAT working export."""

    def __init__(self, parent: tk.Widget, app: MainApp, project_id: int) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.project_id = project_id
        self.project = self.db.get_project(project_id)
        today = date.today()
        self.period_from_var = tk.StringVar(value=today.replace(day=1).strftime("%d.%m.%Y"))
        self.period_to_var = tk.StringVar(value=today.strftime("%d.%m.%Y"))
        self.report_language_var = tk.StringVar(value=self.app.ui_language.upper())
        self.summary_vars = {
            "output_net": tk.StringVar(value="0,00 EUR"),
            "output_vat": tk.StringVar(value="0,00 EUR"),
            "input_net": tk.StringVar(value="0,00 EUR"),
            "input_vat": tk.StringVar(value="0,00 EUR"),
            "vat_payable": tk.StringVar(value="0,00 EUR"),
        }
        self.document_count_var = tk.StringVar(value="")
        self.warning_var = tk.StringVar(value="")
        self.title("PDV evidencija projekta")
        self.configure(background=BG)
        self._build()
        self.refresh_preview(show_error=False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 850, 500)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        header = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="PDV evidencija projekta", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(self.project.get("name") or "Projekat") + " | Radni izvoz za proveru sa knjigovođom.",
            style="Help.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        period = ttk.LabelFrame(outer, text="Period", padding=10)
        period.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        period.columnconfigure(5, weight=1)
        add_field(period, 0, 0, "Od", self.period_from_var, width=15)
        add_field(period, 0, 2, "Do", self.period_to_var, width=15)
        ttk.Button(period, text="Prikaži pregled", command=self.refresh_preview).grid(row=0, column=4, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(period, text="Datumi: dd.mm.gggg", style="Help.TLabel").grid(row=0, column=5, sticky="w", pady=3)
        ttk.Label(period, text="Jezik izveštaja", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        language_combo = ttk.Combobox(period, textvariable=self.report_language_var, values=("SR", "EN", "DE", "BG", "RU"), width=6, state="readonly", style="Modern.TCombobox")
        language_combo.grid(row=1, column=2, sticky="w", pady=(8, 0))
        add_tooltip(language_combo, tr("Jezik samo ovog PDV PDF/Excel izvoza. Podrazumevano prati jezik programa."))
        ttk.Label(period, text=tr("Važi samo za ovaj PDF i Excel izvoz."), style="Help.TLabel").grid(row=1, column=4, columnspan=2, sticky="w", pady=(8, 0))

        summary = ttk.LabelFrame(outer, text="Sažetak PDV-a", padding=10)
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for column in range(5):
            summary.columnconfigure(column, weight=1)
        cards = [
            ("Izlazna osnovica", "output_net"),
            ("Izlazni PDV", "output_vat"),
            ("Ulazna osnovica", "input_net"),
            ("Ulazni PDV", "input_vat"),
            ("PDV za uplatu / pretplatu", "vat_payable"),
        ]
        for column, (label, key) in enumerate(cards):
            card = ttk.Frame(summary, style="Total.TFrame", padding=(10, 8))
            card.grid(row=0, column=column, sticky="ew", padx=3, pady=2)
            ttk.Label(card, text=label, style="TotalKey.TLabel", wraplength=135).pack(anchor="w")
            style = "TotalDue.TLabel" if key == "vat_payable" else "TotalValue.TLabel"
            ttk.Label(card, textvariable=self.summary_vars[key], style=style).pack(anchor="w", pady=(2, 0))
        ttk.Label(summary, textvariable=self.document_count_var, style="Help.TLabel").grid(row=1, column=0, columnspan=5, sticky="w", padx=4, pady=(8, 0))

        controls = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        controls.grid(row=3, column=0, sticky="ew")
        ttk.Label(
            controls,
            text="Izlaz sadrži: fakture, ručne izlazne račune i kreditna odobrenja. Ulaz sadrži troškove i ulazne račune."
            " Ne predstavlja direktnu prijavu za NRA.",
            style="Help.TLabel",
            wraplength=790,
        ).pack(anchor="w")
        ttk.Label(controls, textvariable=self.warning_var, style="Help.TLabel", wraplength=790).pack(anchor="w", pady=(6, 0))

        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="Napravi PDF i Excel", style="Primary.TButton", command=self.generate).pack(side="left")
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="right")

    def refresh_preview(self, *, show_error: bool = True) -> bool:
        try:
            report = self.db.project_vat_evidence(self.project_id, self.period_from_var.get(), self.period_to_var.get())
        except ValueError as exc:
            if show_error:
                messagebox.showerror("PDV evidencija", str(exc), parent=self)
            return False
        self.report = report
        totals = report["totals"]
        for key in self.summary_vars:
            self.summary_vars[key].set(fmt_money(totals.get(key) or 0, DEFAULT_CURRENCY))
        self.document_count_var.set(
            f"Izlazni dokumenti: {totals['output_document_count']} | Ulazni dokumenti: {totals['input_document_count']}"
        )
        foreign = len(report.get("foreign_currency_rows") or [])
        missing = len(report.get("missing_date_rows") or [])
        if foreign or missing:
            self.warning_var.set(
                f"Kontrola pre izvoza: van EUR {foreign}; bez datuma {missing}. Ove stavke nisu u zbiru PDV-a i biće na listu Kontrola."
            )
        else:
            self.warning_var.set("Kontrola: sve stavke u izabranom periodu su u EUR i imaju datum.")
        return True

    def generate(self) -> None:
        try:
            report, task = self.app.prepare_project_vat_evidence_task(
                self.project_id,
                self.period_from_var.get(),
                self.period_to_var.get(),
                self.report_language_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("PDV evidencija", str(exc), parent=self)
            return

        def complete(bundle: dict[str, Path]) -> None:
            open_path(bundle["pdf"])
            messagebox.showinfo(
                "PDV evidencija je spremna",
                f"PDF i Excel su sačuvani u folderu projekta:\n{bundle['pdf']}",
                parent=self.app,
            )
            self.destroy()

        self.app.run_pdf_export(
            title="Priprema PDV evidencije projekta",
            task=task,
            on_success=complete,
        )


class ProjectPeriodOverviewDialog(tk.Toplevel):
    """Show only the six project numbers a first-time user needs for a period."""

    def __init__(self, parent: tk.Widget, app: MainApp, project_id: int) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.project_id = project_id
        self.project = self.db.get_project(project_id)
        today = date.today()
        self.period_from_var = tk.StringVar(value=today.replace(day=1).strftime("%d.%m.%Y"))
        self.period_to_var = tk.StringVar(value=today.strftime("%d.%m.%Y"))
        self.caption_var = tk.StringVar()
        self.values = {
            "income_net": tk.StringVar(value="0,00 EUR"),
            "expense_net": tk.StringVar(value="0,00 EUR"),
            "paid_total": tk.StringVar(value="0,00 EUR"),
            "open_invoice_total": tk.StringVar(value="0,00 EUR"),
            "vat_payable": tk.StringVar(value="0,00 EUR"),
            "profit_net": tk.StringVar(value="0,00 EUR"),
        }
        self.title("Pregled zarade projekta")
        self.configure(background=BG)
        self._build()
        self.refresh_preview(show_error=False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 860, 470)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        header = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Pregled zarade projekta", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(self.project.get("name") or "Projekat") + " | Prihod i zarada su bez PDV-a.",
            style="Help.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        period = ttk.LabelFrame(outer, text="Period", padding=10)
        period.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        period.columnconfigure(5, weight=1)
        add_field(period, 0, 0, "Od", self.period_from_var, width=15)
        add_field(period, 0, 2, "Do", self.period_to_var, width=15)
        ttk.Button(period, text="Prikaži pregled", style="Primary.TButton", command=self.refresh_preview).grid(row=0, column=4, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(period, text="Datumi: dd.mm.gggg", style="Help.TLabel").grid(row=0, column=5, sticky="w", pady=3)

        cards = ttk.Frame(outer, style="App.TFrame")
        cards.grid(row=2, column=0, sticky="ew")
        for column in range(3):
            cards.columnconfigure(column, weight=1)
        labels = [
            ("Prihod bez PDV-a", "income_net"),
            ("Troškovi bez PDV-a", "expense_net"),
            ("Naplaćeno", "paid_total"),
            ("Dugovanja iz perioda", "open_invoice_total"),
            ("PDV za uplatu / pretplatu", "vat_payable"),
            ("Zarada bez PDV-a", "profit_net"),
        ]
        for index, (label, key) in enumerate(labels):
            card = ttk.Frame(cards, style="Total.TFrame", padding=(12, 10))
            card.grid(row=index // 3, column=index % 3, sticky="ew", padx=4, pady=4)
            ttk.Label(card, text=label, style="TotalKey.TLabel", wraplength=190).pack(anchor="w")
            ttk.Label(card, textvariable=self.values[key], style="TotalDue.TLabel" if key in {"vat_payable", "profit_net"} else "TotalValue.TLabel").pack(anchor="w", pady=(3, 0))

        ttk.Label(outer, textvariable=self.caption_var, style="Help.TLabel", wraplength=810).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(outer, text="Zatvori", command=self.destroy).grid(row=4, column=0, sticky="e", pady=(12, 0))

    def refresh_preview(self, *, show_error: bool = True) -> bool:
        try:
            summary = self.db.project_period_summary(self.project_id, self.period_from_var.get(), self.period_to_var.get())
        except ValueError as exc:
            if show_error:
                messagebox.showerror("Pregled zarade", str(exc), parent=self)
            return False
        for key, variable in self.values.items():
            variable.set(fmt_money(summary.get(key) or 0, DEFAULT_CURRENCY))
        self.caption_var.set(
            f"Dokumenti u periodu: izlazni {summary['invoice_count']} | ulazni {summary['input_document_count']}. "
            "Dugovanja prikazuju otvoren iznos faktura iz izabranog perioda."
        )
        return True


class ProjectPaymentPickerDialog(tk.Toplevel):
    """Choose the invoice first so the main payment button works without hunting in the ledger."""

    def __init__(self, parent: tk.Widget, app: MainApp, project_id: int, on_selected: Callable[[int], None]) -> None:
        super().__init__(parent)
        self.app = app
        self.project_id = project_id
        self.on_selected = on_selected
        self.invoice_ids: dict[str, int] = {}
        self.title("Izaberite fakturu za uplatu")
        self.configure(background=BG)
        self._build()
        self.refresh()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 800, 440)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Dodaj uplatu", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.tree = ttk.Treeview(outer, columns=("number", "customer", "due", "balance"), show="headings", height=10)
        setup_treeview_tree(self.tree)
        for key, label, width, anchor in [
            ("number", "Broj fakture", 150, "w"),
            ("customer", "Kupac", 245, "w"),
            ("due", "Rok", 115, "w"),
            ("balance", "Otvoreno", 140, "e"),
        ]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.tree.bind("<Double-1>", lambda _event: self.select())
        self.tree.bind("<Return>", lambda _event: self.select())
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Nastavi", style="Primary.TButton", command=self.select).pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.invoice_ids.clear()
        rows = [
            row for row in self.app.db.list_invoices(project_id=self.project_id)
            if row.get("status_code") not in {"draft", "cancelled", "paid"} and float(row.get("balance_total") or 0) > 0
        ]
        for index, row in enumerate(rows):
            iid = f"invoice:{row['id']}"
            self.invoice_ids[iid] = int(row["id"])
            self.tree.insert(
                "", "end", iid=iid,
                values=(row.get("invoice_number"), row.get("customer_name"), display_date(row.get("due_date")), fmt_money(row.get("balance_total") or 0)),
                tags=(tree_row_tag(index),),
            )
        if rows:
            self.tree.selection_set(f"invoice:{rows[0]['id']}")

    def select(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Dodaj uplatu", "Izaberite fakturu za uplatu.", parent=self)
            return
        invoice_id = self.invoice_ids.get(selected[0])
        if not invoice_id:
            return
        self.destroy()
        self.on_selected(invoice_id)


class ProjectStartGuideDialog(tk.Toplevel):
    """Keep onboarding as a short checklist instead of introducing accounting jargon."""

    def __init__(self, parent: tk.Widget, finance: "ProjectFinanceDialog") -> None:
        super().__init__(parent)
        self.finance = finance
        self.app = finance.app
        self.title("Početni vodič projekta")
        self.configure(background=BG)
        self.status_vars = [tk.StringVar() for _ in range(5)]
        self._build()
        self.refresh()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 820, 500)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="Početni vodič za projekat", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            outer,
            text="Idite redom. Svaki korak ostaje bezbedno sačuvan u projektu.",
            style="Help.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 12))
        steps = [
            ("1.", "Dodaj kupca", "Kupac se koristi za automatsko popunjavanje fakture.", self.finance.add_customer_to_project),
            ("2.", "Dodaj projekat", "Ovaj projekat je već otvoren i vodi svu dokumentaciju.", None),
            ("3.", "Unesi ulazni račun", "Ulazni račun = vaš trošak na projektu.", lambda: self.finance.open_document("input")),
            ("4.", "Izdaj fakturu", "Izlazna faktura = vaš prihod od kupca.", self.finance.new_project_invoice),
            ("5.", "Evidentiraj uplatu", "Kada kupac plati, povežite uplatu sa njegovom fakturom.", self.finance.add_project_payment),
        ]
        self.actions: list[Callable[[], None] | None] = []
        for row, (number, title, hint, action) in enumerate(steps, start=2):
            ttk.Label(outer, text=number, style="CardValue.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 10), pady=5)
            block = ttk.Frame(outer, style="Panel.TFrame", padding=9)
            block.grid(row=row, column=1, sticky="ew", pady=5)
            ttk.Label(block, text=title, style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(block, text=hint, style="Help.TLabel", wraplength=430).pack(anchor="w", pady=(2, 0))
            ttk.Label(block, textvariable=self.status_vars[row - 2], style="Help.TLabel").pack(anchor="w", pady=(4, 0))
            button = ttk.Button(outer, text="Otvori" if action else "Završeno", command=lambda fn=action: self.run_action(fn))
            if action is None:
                button.configure(state="disabled")
            button.grid(row=row, column=2, sticky="e", padx=(10, 0), pady=5)
            self.actions.append(action)
        ttk.Button(outer, text="Zatvori", command=self.destroy).grid(row=7, column=2, sticky="e", pady=(12, 0))

    def refresh(self) -> None:
        project = self.finance.db.get_project(self.finance.project_id)
        summary = self.finance.db.project_financial_summary(self.finance.project_id)
        has_payment = any(float(row.get("paid_total") or 0) > 0 for row in self.finance.db.list_invoices(project_id=self.finance.project_id))
        states = [
            bool(project.get("customer_id")),
            True,
            summary.get("input_document_count", 0) > 0,
            summary.get("issued_invoice_count", 0) > 0,
            has_payment,
        ]
        for variable, complete in zip(self.status_vars, states):
            variable.set("Završeno" if complete else "Sledeći korak")

    def run_action(self, action: Callable[[], None] | None) -> None:
        if action is None:
            return
        action()
        self.after(250, self.refresh)


class ProjectRemindersDialog(tk.Toplevel):
    """Show deadline, attachment and budget follow-ups in a single actionable list."""

    def __init__(self, parent: tk.Widget, finance: "ProjectFinanceDialog") -> None:
        super().__init__(parent)
        self.finance = finance
        self.app = finance.app
        self.rows: dict[str, tuple[str, int | None]] = {}
        self.title("Podsetnici projekta")
        self.configure(background=BG)
        self._build()
        self.refresh()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 900, 540)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Podsetnici projekta", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.tree = ttk.Treeview(outer, columns=("kind", "document", "detail", "amount"), show="headings", height=13)
        setup_treeview_tree(self.tree)
        for key, label, width, anchor in [
            ("kind", "Podsetnik", 180, "w"),
            ("document", "Dokument / grupa", 180, "w"),
            ("detail", "Detalj", 360, "w"),
            ("amount", "Iznos", 125, "e"),
        ]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Otvori stavku", style="Primary.TButton", command=self.open_selected).pack(side="left")
        ttk.Button(buttons, text="Pošalji podsetnik", command=self.send_selected_reminder).pack(side="left", padx=6)
        ttk.Button(buttons, text="Osveži", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        reminders = self.finance.db.project_reminders(self.finance.project_id)
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.rows.clear()
        entries: list[tuple[str, str, str, str, tuple[str, int | None]]] = []
        for row in reminders["due_soon"]:
            entries.append((tr("Dospeva u 7 dana"), row.get("invoice_number") or "", f"{row.get('customer_name') or '-'} | {tr('Rok {date}').format(date=display_date(row.get('due_date')))}", fmt_money(row.get("balance_total") or 0), ("invoice", int(row["id"]))))
        for row in reminders["overdue"]:
            entries.append((tr("Dospeli kupac"), row.get("invoice_number") or "", f"{row.get('customer_name') or '-'} | {tr('Rok {date}').format(date=display_date(row.get('due_date')))}", fmt_money(row.get("balance_total") or 0), ("invoice", int(row["id"]))))
        for row in reminders["missing_pdf"]:
            entries.append((tr("Ulazni račun bez PDF-a"), row.get("document_no") or tr("Bez broja"), f"{row.get('partner_name') or '-'} | {row.get('description') or ''}", fmt_money(row.get("gross_amount") or 0), ("archive", None)))
        for row in reminders["over_budget"]:
            entries.append((tr("Trošak iznad budžeta"), row.get("group") or "", tr("Uneti trošak je veći od planiranog budžeta."), fmt_money(row.get("variance_net") or 0), ("budget", None)))
        if not entries:
            entries.append((tr("Sve je pod kontrolom"), "-", tr("Nema dospelih faktura, rokova, PDF provera niti prekoračenja budžeta."), "", ("none", None)))
        for index, (kind, document, detail, amount, target) in enumerate(entries):
            iid = f"reminder:{index}"
            self.rows[iid] = target
            self.tree.insert("", "end", iid=iid, values=(kind, document, detail, amount), tags=(tree_row_tag(index),))

    def open_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Podsetnici", "Izaberite stavku iz liste.", parent=self)
            return
        target, identifier = self.rows.get(selected[0], ("none", None))
        if target == "invoice" and identifier:
            self.app.open_invoice_editor(identifier)
            self.refresh()
        elif target == "archive":
            ProjectArchiveDialog(self, self.app, self.finance.project_id)
        elif target == "budget":
            ProjectBudgetDialog(self, self.app, self.finance.project_id, on_saved=self.finance.refresh)

    def send_selected_reminder(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Podsetnik", "Izaberite fakturu kupca iz liste.", parent=self)
            return
        target, identifier = self.rows.get(selected[0], ("none", None))
        if target != "invoice" or not identifier:
            messagebox.showinfo("Podsetnik", "Podsetnik može da se pošalje samo za izdatu otvorenu fakturu.", parent=self)
            return
        PaymentReminderDialog(self, self.app, identifier, on_sent=self.refresh)


class PaymentReminderDialog(tk.Toplevel):
    """Compose a payment reminder and keep an audit trail after it is sent."""

    def __init__(self, parent: tk.Widget, app: MainApp, invoice_id: int, *, on_sent: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.invoice_id = invoice_id
        self.on_sent = on_sent
        self.invoice = self.db.get_invoice(invoice_id)
        self.company = self.db.get_company()
        self.title("Podsetnik za plaćanje")
        self.configure(background=BG)
        self.resizable(True, True)
        if not self.invoice:
            self.destroy()
            return
        self.recipient_var = tk.StringVar(value=str(self.invoice.get("customer_email") or "").strip())
        invoice_no = str(self.invoice.get("invoice_number") or "").strip()
        self.subject_var = tk.StringVar(value=payment_reminder_copy("subject", number=invoice_no))
        self._build()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 820, 520)
        localize_widget_tree(self, self.app.ui_language)

    def _default_body(self) -> str:
        due = display_date(self.invoice.get("due_date"))
        amount = fmt_money(self.invoice.get("balance_total") or 0, self.invoice.get("currency") or DEFAULT_CURRENCY)
        customer = self.invoice.get("customer_name") or ""
        return payment_reminder_copy(
            "body",
            customer=customer,
            number=self.invoice.get("invoice_number") or "-",
            amount=amount,
            due=due,
            company=self.company.get("name") or APP_NAME,
        )

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="Podsetnik kupcu", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text=tr("Faktura {number} | otvoreno {amount} | rok {date}").format(
                number=self.invoice.get("invoice_number") or "-",
                amount=fmt_money(self.invoice.get("balance_total") or 0, self.invoice.get("currency") or DEFAULT_CURRENCY),
                date=display_date(self.invoice.get("due_date")),
            ),
            style="Help.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 12))
        add_field(outer, 2, 0, "Primaoc", self.recipient_var, width=48)
        add_field(outer, 3, 0, "Naslov", self.subject_var, width=54)
        ttk.Label(outer, text="Poruka", style="Field.TLabel").grid(row=4, column=0, sticky="nw", padx=(0, 8), pady=4)
        self.body = tk.Text(outer, height=11, wrap="word", background="white", foreground=TEXT, insertbackground=TEXT, relief="solid", borderwidth=1, highlightthickness=1, highlightbackground=LINE)
        self.body.grid(row=4, column=1, sticky="nsew", pady=4)
        self.body.insert("1.0", self._default_body())
        outer.rowconfigure(4, weight=1)
        ttk.Label(outer, text="Slanje se beleži u istoriji fakture. Faktura i prilozi se ne šalju automatski.", style="Help.TLabel", wraplength=690).grid(row=5, column=0, columnspan=2, sticky="w", pady=(7, 0))
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Pošalji podsetnik", style="Primary.TButton", command=self.send).pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")

    def send(self) -> None:
        status_code = str(self.invoice.get("status_code") or "draft")
        if status_code not in {"issued", "partial", "paid", "due"}:
            messagebox.showinfo(
                tr("Podsetnik"),
                tr("Podsetnik može da se šalje tek kada se faktura izda."),
                parent=self,
            )
            return
        recipient = self.recipient_var.get().strip()
        subject = self.subject_var.get().strip()
        body = self.body.get("1.0", "end").strip()
        if not recipient or "@" not in recipient:
            messagebox.showerror(tr("Podsetnik"), tr("Unesite ispravan e-mail primaoca."), parent=self)
            return
        sender_name = str(self.company.get("smtp_from_name") or self.company.get("name") or APP_NAME).strip()
        sender_email = str(self.company.get("smtp_from_email") or self.company.get("email") or self.company.get("smtp_username") or "").strip()
        if not sender_email:
            messagebox.showerror("Podsetnik", "Podesite SMTP pošiljaoca u podacima firme.", parent=self)
            return
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = formataddr((sender_name, sender_email))
        message["Subject"] = subject
        if self.company.get("smtp_reply_to"):
            message["Reply-To"] = str(self.company["smtp_reply_to"])
        message.set_content(body)
        try:
            send_message_via_smtp(self.company, message)
            self.db.record_payment_reminder(self.invoice_id, recipient, subject)
        except Exception as exc:
            messagebox.showerror("Podsetnik", f"Slanje nije uspelo:\n{exc}", parent=self)
            return
        messagebox.showinfo(tr("Podsetnik"), tr("Podsetnik je poslat i upisan u istoriju fakture."), parent=self)
        if self.on_sent:
            self.on_sent()
        self.destroy()


class MonthlyControlChecklistDialog(tk.Toplevel):
    """Persistent month-end controls that make a handover and period close reviewable."""

    STATUS_LABELS = {"pending": "Na čekanju", "done": "Završeno", "blocked": "Blokirano"}

    def __init__(self, parent: tk.Widget, app: MainApp, *, on_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.on_changed = on_changed
        self.period_var = tk.StringVar(value=date.today().strftime("%Y-%m"))
        self.rows: dict[str, dict[str, Any]] = {}
        self.title("Mesečna kontrola")
        self.configure(background=BG)
        self._build()
        self.refresh()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 1120, 620)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(2, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Mesečna kontrola i zatvaranje", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text="Period se zaključava tek kada su kontrole završene ili dokumentovani izuzeci prosleđeni vlasniku i lokalnom računovođi.",
            style="Help.TLabel", wraplength=1040,
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))
        controls = ttk.Frame(outer, style="App.TFrame")
        controls.grid(row=2, column=0, sticky="nsew")
        controls.rowconfigure(1, weight=1)
        controls.columnconfigure(0, weight=1)
        bar = ttk.Frame(controls, style="App.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(bar, text="Period (gggg-mm)", style="Field.TLabel").pack(side="left", padx=(0, 6))
        ttk.Entry(bar, textvariable=self.period_var, width=10, style="Modern.TEntry").pack(side="left")
        ttk.Button(bar, text="Učitaj", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(bar, text="Napravi i proveri backup", command=self.verify_backup).pack(side="left")
        ttk.Button(bar, text="Proveri audit lanac", command=self.verify_audit_chain).pack(side="left", padx=6)
        ttk.Button(bar, text="Zaključi obračunski period", command=self.open_period_close).pack(side="left", padx=6)
        ttk.Button(bar, text="Ponovo otvori period", command=self.reopen_period).pack(side="left")
        table = ttk.Frame(controls, style="Panel.TFrame", padding=8)
        table.grid(row=1, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("status", "owner", "task", "note", "completed"), show="headings", selectmode="browse")
        setup_treeview_tree(self.tree)
        for key, label, width in (
            ("status", "Status", 110), ("owner", "Odgovoran", 165), ("task", "Kontrola", 470),
            ("note", "Komentar / izuzetak", 260), ("completed", "Potvrdio", 160),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=key in {"task", "note"})
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        actions = ttk.Frame(outer, style="App.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Označi završeno", style="Primary.TButton", command=lambda: self.set_selected("done")).pack(side="left")
        ttk.Button(actions, text="Blokiraj sa komentarom", command=lambda: self.set_selected("blocked")).pack(side="left", padx=6)
        ttk.Button(actions, text="Vrati na čekanje", command=lambda: self.set_selected("pending")).pack(side="left")
        ttk.Button(actions, text="Osveži", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(actions, text="Zatvori", command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        try:
            rows = self.app.db.monthly_control_checklist(self.period_var.get())
        except ValueError as exc:
            messagebox.showerror("Mesečna kontrola", str(exc), parent=self)
            return
        self.period_var.set(rows[0]["period_key"])
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.rows.clear()
        for index, row in enumerate(rows):
            iid = str(row["code"])
            self.rows[iid] = row
            completed = " · ".join(part for part in (row.get("completed_by"), str(row.get("completed_at") or "").replace("T", " ")) if part)
            self.tree.insert(
                "", "end", iid=iid,
                values=(self.STATUS_LABELS.get(row.get("status"), row.get("status")), row.get("owner_role"), row.get("label"), row.get("note"), completed),
                tags=(tree_row_tag(index),),
            )

    def verify_backup(self) -> None:
        """Make the month-end backup check evidence-based instead of a tick box."""
        if not self.app.require_team_permission({"owner", "administrator"}, "provera backupa", parent=self):
            return
        try:
            report = self.app.db.create_and_verify_backup()
            sync = self.app.db.cloud_sync_state()
            sync_note = (
                f"Poslednja centralna sinhronizacija: {sync.get('last_sync_at')}."
                if sync.get("last_sync_at")
                else "Centralna sinhronizacija nije potvrđena na ovom računaru."
            )
            note = (
                f"Automatski testiran backup {report['name']} ({int(report['size']):,} B). "
                f"{report['detail']} {sync_note}"
            )
            self.app.db.set_monthly_control_task(
                self.period_var.get(),
                "backup_verified",
                "done",
                note=note,
                completed_by=self.app.active_team_member_name(),
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Backup", f"Backup nije potvrđen:\n{exc}", parent=self)
            return
        self.app.refresh_all()
        self.refresh()
        messagebox.showinfo(
            "Backup",
            "Backup je napravljen i pročitan kroz SQLite proveru integriteta. Kontrola je upisana u mesečni trag.",
            parent=self,
        )

    def verify_audit_chain(self) -> None:
        """Turn the audit-integrity control into monthly evidence, not a claim."""
        if not self.app.require_team_permission({"owner", "administrator", "accountant"}, "provera finansijskog audita", parent=self):
            return
        report = self.app.db.verify_financial_audit_chain()
        if not report["ok"]:
            messagebox.showerror(
                "Finansijski audit",
                f"{report['detail']} Ne zaključavajte period; sačuvajte provereni backup i prijavite administratoru.",
                parent=self,
            )
            return
        note = f"{report['detail']} Poslednji hash: {report['last_hash'] or '-'}"
        try:
            self.app.db.set_monthly_control_task(
                self.period_var.get(),
                "audit_integrity_verified",
                "done",
                note=note,
                completed_by=self.app.active_team_member_name(),
            )
        except ValueError as exc:
            messagebox.showerror("Finansijski audit", str(exc), parent=self)
            return
        self.app.refresh_all()
        self.refresh()
        messagebox.showinfo("Finansijski audit", "Audit lanac je provereno ispravan i kontrola je upisana u mesečni trag.", parent=self)

    def set_selected(self, status: str) -> None:
        selected = self.tree.selection()
        if not selected or selected[0] not in self.rows:
            messagebox.showinfo("Mesečna kontrola", "Izaberite kontrolnu stavku.", parent=self)
            return
        row = self.rows[selected[0]]
        if row.get("code") == "backup_verified" and status == "done":
            # This control is evidence-based: a plain manual tick would make
            # a month-end close appear safer than it actually is.
            self.verify_backup()
            return
        if row.get("code") == "audit_integrity_verified" and status == "done":
            # The hash-chain check must be run now; an old comment is not
            # evidence that the current financial trail is intact.
            self.verify_audit_chain()
            return
        current_note = str(row.get("note") or "")
        note = current_note
        if status == "blocked":
            result = simpledialog.askstring("Blokirana kontrola", "Napišite razlog i sledeći korak:", initialvalue=current_note, parent=self)
            if result is None:
                return
            note = result
        elif status == "pending" and current_note:
            result = simpledialog.askstring("Vrati na čekanje", "Komentar (opciono):", initialvalue=current_note, parent=self)
            if result is None:
                return
            note = result
        try:
            self.app.db.set_monthly_control_task(
                self.period_var.get(), row["code"], status,
                note=note,
                completed_by=self.app.active_team_member_name(),
            )
        except ValueError as exc:
            messagebox.showerror("Mesečna kontrola", str(exc), parent=self)
            return
        self.app.refresh_all()
        self.refresh()
        if self.on_changed:
            self.on_changed()

    def open_period_close(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator"}, "zaključavanje obračunskog perioda", parent=self):
            return
        try:
            rows = self.app.db.monthly_control_checklist(self.period_var.get())
        except ValueError as exc:
            messagebox.showerror("Mesečna kontrola", str(exc), parent=self)
            return
        incomplete = [row for row in rows if row.get("status") != "done"]
        if incomplete:
            messagebox.showwarning(
                "Mesečna kontrola",
                f"Pre zaključavanja je ostalo {len(incomplete)} kontrola. Završite ih ili ih označite kao blokirane sa komentarom.",
                parent=self,
            )
            return
        year, month = (int(part) for part in self.period_var.get().split("-"))
        start = date(year, month, 1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
        dialog = FinancialRecordDialog(self, self.app, "period", on_saved=self.refresh)
        dialog.vars["period_from"].set(start.isoformat())
        dialog.vars["period_to"].set(end.isoformat())
        dialog.vars["status"].set("closed")
        dialog.vars["note"].set("Mesečna kontrola završena u OpsNest-u.")

    def reopen_period(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator"}, "ponovno otvaranje obračunskog perioda", parent=self):
            return
        try:
            year, month = (int(part) for part in self.period_var.get().split("-"))
        except ValueError:
            messagebox.showerror("Mesečna kontrola", "Period unesite u formatu gggg-mm.", parent=self)
            return
        start = date(year, month, 1).isoformat()
        next_month = (date(year, month, 1).replace(day=28) + timedelta(days=4)).replace(day=1)
        end = (next_month - timedelta(days=1)).isoformat()
        period = next(
            (
                row for row in self.app.db.list_accounting_periods()
                if row.get("status") == "closed" and row.get("period_from") == start and row.get("period_to") == end
            ),
            None,
        )
        if not period:
            messagebox.showinfo("Ponovno otvaranje", "Za izabrani mesec nema zaključenog obračunskog perioda.", parent=self)
            return
        reason = simpledialog.askstring(
            "Ponovno otvaranje perioda",
            "Razlog ponovnog otvaranja (obavezno, ulazi u finansijski audit):",
            parent=self,
        )
        if reason is None:
            return
        if not reason.strip():
            messagebox.showwarning("Ponovno otvaranje", "Unesite razlog ponovnog otvaranja.", parent=self)
            return
        if not messagebox.askyesno(
            "Potvrda kontrole",
            f"Ponovo otvoriti period {self.period_var.get()}? Sve naredne izmene biće evidentirane u finansijskom auditu.",
            parent=self,
        ):
            return
        try:
            self.app.db.reopen_accounting_period(
                int(period["id"]),
                reopened_by=self.app.active_team_member_name(),
                reason=reason,
            )
        except ValueError as exc:
            messagebox.showerror("Ponovno otvaranje", str(exc), parent=self)
            return
        self.app.refresh_all()
        self.refresh()
        if self.on_changed:
            self.on_changed()
        messagebox.showinfo("Ponovno otvaranje", "Period je ponovo otvoren i evidentiran u finansijskom auditu.", parent=self)


class DailyWorkCenterDialog(tk.Toplevel):
    """One cross-project control queue for daily work and safe handovers."""

    def __init__(self, parent: tk.Widget, app: MainApp) -> None:
        super().__init__(parent)
        self.app = app
        self.rows: dict[str, tuple[str, int | None, int | None]] = {}
        self.summary_var = tk.StringVar()
        self.title("Operativni centar")
        self.configure(background=BG)
        self._build()
        self.refresh()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        maximize_large_window(self, minimum_width=980, minimum_height=620)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Operativni centar", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text="Jedan radni red za vlasnika, administratora i knjigovodstvo. Svaka stavka vodi na izvorni dokument ili odgovarajući tok.",
            style="Help.TLabel", wraplength=1050,
        ).grid(row=0, column=0, sticky="w", pady=(28, 0))
        ttk.Label(outer, textvariable=self.summary_var, style="Value.TLabel").grid(row=0, column=0, sticky="e")
        self.tree = ttk.Treeview(outer, columns=("priority", "project", "document", "partner", "deadline", "amount"), show="headings")
        setup_treeview_tree(self.tree)
        for key, title, width, anchor in [
            ("priority", "Obaveza", 185, "w"), ("project", "Projekat", 160, "w"), ("document", "Dokument", 135, "w"),
            ("partner", "Kupac / dobavljač", 190, "w"), ("deadline", "Rok / detalj", 180, "w"), ("amount", "Iznos", 125, "e"),
        ]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())
        actions = ttk.Frame(outer, style="App.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Otvori", style="Primary.TButton", command=self.open_selected).pack(side="left")
        ttk.Button(actions, text="Pošalji podsetnik", command=self.send_selected_reminder).pack(side="left", padx=6)
        ttk.Button(actions, text="Mesečna kontrola", command=self.open_monthly_control).pack(side="left")
        ttk.Button(actions, text="Priručnik za smenu", command=self.open_runbook).pack(side="left", padx=6)
        ttk.Button(actions, text="Osveži", command=self.refresh).pack(side="left")
        ttk.Button(actions, text="Zatvori", command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        data = self.app.db.daily_work_center()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.rows.clear()
        entries: list[tuple[str, str, str, str, str, str, tuple[str, int | None, int | None]]] = []
        for row in data["pending_invoice_approvals"]:
            entries.append(("Faktura čeka odobrenje", row.get("project_name") or "-", row.get("invoice_number") or "-", row.get("customer_name") or "-", "Vlasnik / administrator treba da pregleda", fmt_money(row.get("gross_total") or 0, row.get("currency") or DEFAULT_CURRENCY), ("invoice_approval", int(row["id"]), int(row.get("project_id") or 0))))
        for row in data["returned_for_revision"]:
            entries.append(("Faktura vraćena na doradu", row.get("project_name") or "-", row.get("invoice_number") or "-", row.get("customer_name") or "-", "Otvorite istoriju, ispravite nacrt i ponovo pošaljite", fmt_money(row.get("gross_total") or 0, row.get("currency") or DEFAULT_CURRENCY), ("invoice", int(row["id"]), int(row.get("project_id") or 0))))
        for row in data["pending_vendor_approvals"]:
            entries.append(("Obaveza čeka odobrenje", row.get("project_name") or "-", row.get("bill_number") or "Bez broja", row.get("vendor_name") or "-", "Proverite dokument i odobrite ili vratite na proveru", fmt_money(row.get("balance_amount") or 0, row.get("currency") or DEFAULT_CURRENCY), ("vendor_bill", int(row["id"]), int(row.get("project_id") or 0))))
        for row in data["vendor_evidence_missing"]:
            entries.append(("Obaveza bez originalnog dokumenta", row.get("project_name") or "-", row.get("bill_number") or "Bez broja", row.get("vendor_name") or "-", "Priložite originalni račun ili povežite ulazni dokument pre odobrenja i plaćanja", fmt_money(row.get("balance_amount") or 0, row.get("currency") or DEFAULT_CURRENCY), ("vendor_bill", int(row["id"]), int(row.get("project_id") or 0))))
        for row in data["rejected_vendor_bills"]:
            entries.append(("Obaveza vraćena na doradu", row.get("project_name") or "-", row.get("bill_number") or "Bez broja", row.get("vendor_name") or "-", row.get("rejection_reason") or "Proverite komentar i ponovo pošaljite na proveru", fmt_money(row.get("balance_amount") or 0, row.get("currency") or DEFAULT_CURRENCY), ("vendor_bill", int(row["id"]), int(row.get("project_id") or 0))))
        for row in data["bank_review"]:
            direction = "Odliv" if row.get("direction") == "outflow" else "Priliv"
            entries.append((f"Banka: {direction.lower()} za proveru", "-", row.get("reference") or row.get("source_file") or "Bankovni izvod", row.get("payer_name") or "-", row.get("match_reason") or "Povežite ili označite stavku", fmt_money(row.get("amount") or 0, row.get("currency") or DEFAULT_CURRENCY), ("bank", int(row["id"]), None)))
        for row in data["overdue"]:
            entries.append((tr("Dospeli kupac"), row.get("project_name") or "-", row.get("invoice_number") or "-", row.get("customer_name") or "-", tr("Rok {date}").format(date=display_date(row.get("due_date"))), fmt_money(row.get("balance_total") or 0, row.get("currency") or DEFAULT_CURRENCY), ("invoice", int(row["id"]), int(row.get("project_id") or 0))))
        for row in data["due_soon"]:
            entries.append((tr("Faktura dospeva uskoro"), row.get("project_name") or "-", row.get("invoice_number") or "-", row.get("customer_name") or "-", tr("Rok {date}").format(date=display_date(row.get("due_date"))), fmt_money(row.get("balance_total") or 0, row.get("currency") or DEFAULT_CURRENCY), ("invoice", int(row["id"]), int(row.get("project_id") or 0))))
        for row in data["missing_pdf"]:
            entries.append((tr("PDF prilog nedostaje"), row.get("project_name") or "-", row.get("document_no") or tr("Bez broja"), row.get("partner_name") or "-", row.get("description") or tr("Ulazni račun"), fmt_money(row.get("gross_amount") or 0, row.get("currency") or DEFAULT_CURRENCY), ("project", None, int(row.get("project_id") or 0))))
        for row in data["over_budget"]:
            entries.append((tr("Budžet prekoračen"), row.get("project_name") or "-", row.get("group") or "-", "-", tr("Trošak je veći od planiranog"), fmt_money(row.get("variance_net") or 0), ("project", None, int(row.get("project_id") or 0))))
        if not entries:
            entries.append((tr("Sve je pod kontrolom"), "-", "-", "-", tr("Nema otvorenih obaveza"), "", ("none", None, None)))
        self.summary_var.set(f"Otvorene akcije: {len(entries)}")
        for index, entry in enumerate(entries):
            iid = f"daily:{index}"
            self.rows[iid] = entry[-1]
            self.tree.insert("", "end", iid=iid, values=entry[:-1], tags=(tree_row_tag(index),))

    def _selected(self) -> tuple[str, int | None, int | None] | None:
        selection = self.tree.selection()
        return self.rows.get(selection[0]) if selection else None

    def open_selected(self) -> None:
        selected = self._selected()
        if not selected:
            messagebox.showinfo("Dnevni centar", "Izaberite stavku iz liste.", parent=self)
            return
        kind, invoice_id, project_id = selected
        if kind == "invoice" and invoice_id:
            self.app.open_invoice_editor(invoice_id)
        elif kind == "invoice_approval":
            self.app.open_invoice_approvals()
        elif kind == "vendor_bill" and invoice_id:
            FinancialRecordDialog(self, self.app, "bill", record_id=invoice_id, on_saved=self.refresh)
        elif kind == "bank":
            self.app.tabs.select(self.app.banking_tab)
            self.app.banking_tab.refresh()
            messagebox.showinfo("Operativni centar", "Otvoren je bankovni pregled. Izaberite stavku i potvrdite ili obradite izuzetak.", parent=self)
        elif kind == "project" and project_id:
            ProjectFinanceDialog(self, self.app, project_id, on_changed=self.refresh)

    def send_selected_reminder(self) -> None:
        selected = self._selected()
        if not selected or selected[0] != "invoice" or not selected[1]:
            messagebox.showinfo("Podsetnik", "Izaberite otvorenu fakturu kupca.", parent=self)
            return
        PaymentReminderDialog(self, self.app, selected[1], on_sent=self.refresh)

    def open_monthly_control(self) -> None:
        MonthlyControlChecklistDialog(self, self.app, on_changed=self.refresh)

    def open_runbook(self) -> None:
        path = APP_DIR / "OPS_NEST_OPERATIONS_RUNBOOK.md"
        if path.is_file():
            open_path(path)
            return
        messagebox.showinfo("Priručnik za smenu", "Priručnik je dostupan uz instalacionu dokumentaciju firme.", parent=self)


class RecurringInvoiceTemplatesDialog(tk.Toplevel):
    """Manage project invoice templates and intentionally create only editable drafts."""

    def __init__(self, parent: tk.Widget, app: MainApp, project_id: int, *, on_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.project_id = project_id
        self.on_changed = on_changed
        self.rows: dict[str, dict[str, Any]] = {}
        self.title("Ponavljajuće fakture")
        self.configure(background=BG)
        self._build()
        self.refresh()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 980, 540)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Ponavljajuće fakture", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text="Na dan dospeća OpsNest kreira nacrt. Pre izdavanja uvek pregledate i potvrdite fakturu.", style="Help.TLabel").grid(row=0, column=1, sticky="e")
        self.tree = ttk.Treeview(outer, columns=("name", "customer", "interval", "next", "last", "state"), show="headings")
        setup_treeview_tree(self.tree)
        for key, label, width in [("name", "Naziv", 250), ("customer", "Kupac", 200), ("interval", "Period", 110), ("next", "Sledeći nacrt", 130), ("last", "Poslednja faktura", 150), ("state", "Status", 100)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Kreiraj dospele nacrte", style="Primary.TButton", command=self.generate_due).pack(side="left")
        ttk.Button(buttons, text="Aktiviraj / pauziraj", command=self.toggle_selected).pack(side="left", padx=6)
        ttk.Button(buttons, text="Otvori poslednji nacrt", command=self.open_last).pack(side="left")
        ttk.Button(buttons, text="Osveži", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.rows.clear()
        for index, row in enumerate(self.app.db.list_recurring_invoice_templates(self.project_id)):
            iid = f"template:{row['id']}"
            self.rows[iid] = row
            last = self.app.db.get_invoice(int(row["last_invoice_id"])) if row.get("last_invoice_id") else {}
            interval = tr("svakih {count} mes.").format(count=row.get("interval_months") or 1)
            state = tr("Aktivna") if row.get("active") else tr("Pauzirana")
            self.tree.insert("", "end", iid=iid, values=(row.get("name") or "-", row.get("customer_name") or "-", interval, display_date(row.get("next_run_date")), last.get("invoice_number") or "-", state), tags=(tree_row_tag(index),))

    def _selected(self) -> dict[str, Any] | None:
        selection = self.tree.selection()
        return self.rows.get(selection[0]) if selection else None

    def generate_due(self) -> None:
        created = self.app.db.generate_due_recurring_invoices(project_id=self.project_id)
        self.refresh()
        self.app.refresh_all()
        messagebox.showinfo("Ponavljajuće fakture", f"Kreirano nacrta: {len(created)}. Nijedan nacrt nije automatski izdat.", parent=self)

    def toggle_selected(self) -> None:
        row = self._selected()
        if not row:
            messagebox.showinfo("Ponavljajuće fakture", "Izaberite šablon.", parent=self)
            return
        self.app.db.set_recurring_invoice_template_active(int(row["id"]), not bool(row.get("active")))
        self.refresh()

    def open_last(self) -> None:
        row = self._selected()
        if not row or not row.get("last_invoice_id"):
            messagebox.showinfo("Ponavljajuće fakture", "Ovaj šablon još nema kreiran nacrt.", parent=self)
            return
        self.app.open_invoice_editor(int(row["last_invoice_id"]))


class RecurringInvoiceSetupDialog(tk.Toplevel):
    """Save the currently edited invoice as a controlled recurring template."""

    def __init__(self, parent: tk.Widget, editor: "InvoiceEditor") -> None:
        super().__init__(parent)
        self.editor = editor
        self.app = editor.app
        self.name_var = tk.StringVar(value=f"{editor.vars['project_name'].get().strip() or 'Faktura'} - mesečno")
        self.interval_var = tk.StringVar(value="1")
        self.next_date_var = tk.StringVar(value=date.today().strftime("%d.%m.%Y"))
        self.title("Sačuvaj kao ponavljajuću fakturu")
        self.configure(background=BG)
        self._build()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 660, 350)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="Ponavljajuća faktura", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(outer, text="Sačuvaćemo trenutne stavke kao šablon. Na svaki rok nastaje nacrt koji se pregleda pre izdavanja.", style="Help.TLabel", wraplength=590).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))
        add_field(outer, 2, 0, "Naziv šablona", self.name_var, width=44)
        add_field(outer, 3, 0, "Ponavlja se na (meseci)", self.interval_var, width=12)
        add_field(outer, 4, 0, "Prvi nacrt", self.next_date_var, width=16)
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Button(buttons, text="Sačuvaj šablon", style="Primary.TButton", command=self.save).pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")

    def save(self) -> None:
        payload = self.editor._collect_invoice_payload("draft")
        if payload is None:
            return
        if not self.editor.item_data:
            messagebox.showerror("Ponavljajuća faktura", "Dodajte najmanje jednu stavku pre čuvanja šablona.", parent=self)
            return
        try:
            interval = int(self.interval_var.get() or 1)
            template_id = self.app.db.create_recurring_invoice_template(
                payload,
                [dict(item) for item in self.editor.item_data],
                name=self.name_var.get(),
                interval_months=interval,
                next_run_date=self.next_date_var.get(),
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Ponavljajuća faktura", str(exc), parent=self)
            return
        messagebox.showinfo("Ponavljajuća faktura", f"Šablon #{template_id} je sačuvan. Buduće fakture se kreiraju kao nacrti.", parent=self)
        self.destroy()


class ProjectAccountantExportDialog(tk.Toplevel):
    """Create one PDF/XLSX period package without the user having to assemble reports."""

    def __init__(self, parent: tk.Widget, app: MainApp, project_id: int) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.project_id = project_id
        self.project = self.db.get_project(project_id)
        today = date.today()
        self.period_from_var = tk.StringVar(value=today.replace(day=1).strftime("%d.%m.%Y"))
        self.period_to_var = tk.StringVar(value=today.strftime("%d.%m.%Y"))
        self.report_language_var = tk.StringVar(value=self.app.ui_language.upper())
        self.summary_var = tk.StringVar()
        self.warning_var = tk.StringVar()
        self.title("Izvoz projekta za knjigovođu")
        self.configure(background=BG)
        self._build()
        self.refresh_preview(show_error=False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 860, 440)
        localize_widget_tree(self, self.app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        header = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Jedan klik za knjigovođu", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(self.project.get("name") or "Projekat") + " | PDF i Excel ostaju arhivirani u ovom projektu.",
            style="Help.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        period = ttk.LabelFrame(outer, text="Period", padding=10)
        period.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        period.columnconfigure(5, weight=1)
        add_field(period, 0, 0, "Od", self.period_from_var, width=15)
        add_field(period, 0, 2, "Do", self.period_to_var, width=15)
        ttk.Button(period, text="Prikaži pregled", command=self.refresh_preview).grid(row=0, column=4, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(period, text="Datumi: dd.mm.gggg", style="Help.TLabel").grid(row=0, column=5, sticky="w", pady=3)
        ttk.Label(period, text="Jezik izveštaja", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        language_combo = ttk.Combobox(period, textvariable=self.report_language_var, values=("SR", "EN", "DE", "BG", "RU"), width=6, state="readonly", style="Modern.TCombobox")
        language_combo.grid(row=1, column=2, sticky="w", pady=(8, 0))
        add_tooltip(language_combo, tr("Jezik samo ovog paketa za knjigovođu. Podrazumevano prati jezik programa."))
        ttk.Label(period, text=tr("Važi samo za ovaj PDF i Excel izvoz."), style="Help.TLabel").grid(row=1, column=4, columnspan=2, sticky="w", pady=(8, 0))
        contents = ttk.LabelFrame(outer, text="Paket sadrži", padding=12)
        contents.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            contents,
            text="Izlazne fakture | Ulazne račune i troškove | Uplate i povraćaje | PDV pregled | Odobrenja i storna | Kontrolu stavki van EUR ili bez datuma",
            style="Help.TLabel",
            wraplength=780,
        ).pack(anchor="w")
        ttk.Label(contents, textvariable=self.summary_var, style="TotalValue.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(contents, textvariable=self.warning_var, style="Help.TLabel", wraplength=780).pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Napravi PDF i Excel", style="Primary.TButton", command=self.generate).pack(side="left")
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="right")

    def refresh_preview(self, *, show_error: bool = True) -> bool:
        try:
            report = self.db.project_accountant_report(self.project_id, self.period_from_var.get(), self.period_to_var.get())
        except ValueError as exc:
            if show_error:
                messagebox.showerror("Izvoz za knjigovođu", str(exc), parent=self)
            return False
        self.report = report
        totals = report["totals"]
        self.summary_var.set(
            f"Izlazni dokumenti: {totals['output_document_count']} | Ulazni dokumenti: {totals['input_document_count']} | "
            f"Uplate/povraćaji: {totals['payment_count']} | Odobrenja: {totals['credit_note_count']} | Storna: {totals['cancelled_invoice_count']}"
        )
        foreign = len(report.get("foreign_currency_rows") or []) + len(report.get("foreign_currency_payments") or [])
        missing = len(report.get("missing_date_rows") or [])
        self.warning_var.set(
            f"Kontrola: van EUR {foreign}; bez datuma {missing}." if foreign or missing else "Kontrola: sve stavke za ovaj izvoz su u EUR i imaju datum."
        )
        return True

    def generate(self) -> None:
        try:
            _report, task = self.app.prepare_project_accountant_task(
                self.project_id,
                self.period_from_var.get(),
                self.period_to_var.get(),
                self.report_language_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Izvoz za knjigovođu", str(exc), parent=self)
            return

        def complete(bundle: dict[str, Path]) -> None:
            open_path(bundle["pdf"])
            messagebox.showinfo(
                "Paket za knjigovođu je spreman",
                f"PDF i Excel su sačuvani u projektu:\n{bundle['pdf']}",
                parent=self.app,
            )
            self.destroy()

        self.app.run_pdf_export(title="Priprema paketa za knjigovođu", task=task, on_success=complete)


class ProjectFinanceDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, app: MainApp, project_id: int, *, on_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.db = app.db
        self.project_id = project_id
        self.on_changed = on_changed
        self.project = self.db.get_project(project_id)
        self.guide_var = tk.StringVar()
        self.reminder_var = tk.StringVar()
        self.title("Dashboard i knjigovodstvo projekta")
        self.configure(background=BG)
        self._build()
        maximize_large_window(self, minimum_width=1080, minimum_height=680)
        localize_widget_tree(self, self.app.ui_language)
        self.refresh()

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=12)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(5, weight=1)
        outer.columnconfigure(0, weight=1)

        title = ttk.Frame(outer, style="App.TFrame")
        title.grid(row=0, column=0, sticky="ew")
        ttk.Label(title, text="Dashboard projekta", style="ProjectDashboard.TLabel").pack(side="left")
        ttk.Label(title, text=self.project.get("name") or "Projekat", style="Section.TLabel").pack(side="left", padx=(12, 0))
        currency = self.app.company.get("default_currency") or DEFAULT_CURRENCY
        contract_value = fmt_money(self.project.get("contract_net_amount", 0), currency)
        details = " | ".join(
            value
            for value in [
                self.project.get("site_address"),
                self.project.get("contract_no"),
                f"Ugovor bez PDV-a: {contract_value}",
            ]
            if value
        )
        ttk.Label(title, text=details, style="Help.TLabel").pack(side="left", padx=10)
        ttk.Button(title, text="Nazad na projekte", command=self.return_to_projects).pack(side="right")

        orientation = ttk.Frame(outer, style="Panel.TFrame", padding=(10, 7))
        orientation.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        orientation.columnconfigure(0, weight=1)
        ttk.Label(orientation, textvariable=self.guide_var, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            orientation,
            text="Ulazni račun = vaš trošak | Izlazna faktura = vaš prihod | PDV za uplatu = izlazni PDV - ulazni PDV",
            style="Help.TLabel",
            wraplength=720,
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(orientation, textvariable=self.reminder_var, style="Help.TLabel").grid(row=2, column=0, sticky="w", pady=(3, 0))
        ttk.Button(orientation, text="Otvori vodič", command=self.open_start_guide).grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 6))
        ttk.Button(orientation, text="Podsetnici", command=self.open_reminders).grid(row=0, column=2, rowspan=2, sticky="e")

        contract_frame = ttk.LabelFrame(outer, text="Ugovor i avans", padding=(10, 6))
        contract_frame.grid(row=2, column=0, sticky="ew", pady=(0, 7))
        self.contract_flow_var = tk.StringVar()
        ttk.Label(contract_frame, textvariable=self.contract_flow_var, style="Help.TLabel", wraplength=1320).pack(anchor="w")

        quick_actions = ttk.LabelFrame(outer, text="Glavne akcije", padding=(8, 6))
        quick_actions.grid(row=3, column=0, sticky="ew", pady=(0, 7))
        for column in range(5):
            quick_actions.columnconfigure(column, weight=1)
        ttk.Button(quick_actions, text="Nova faktura", style="Primary.TButton", command=self.new_project_invoice).grid(row=0, column=0, sticky="ew", padx=3, pady=2)
        ttk.Button(quick_actions, text="Izdaj avans", command=self.new_project_advance).grid(row=0, column=1, sticky="ew", padx=3, pady=2)
        ttk.Button(quick_actions, text="Dodaj trošak / ulazni račun", command=lambda: self.open_document("input")).grid(row=0, column=2, sticky="ew", padx=3, pady=2)
        ttk.Button(quick_actions, text="Dodaj uplatu", command=self.add_project_payment).grid(row=0, column=3, sticky="ew", padx=3, pady=2)
        ttk.Button(quick_actions, text="Pregled zarade", command=self.open_period_overview).grid(row=0, column=4, sticky="ew", padx=3, pady=2)

        tools = ttk.Frame(outer, style="App.TFrame")
        tools.grid(row=4, column=0, sticky="ew", pady=(0, 7))
        ttk.Button(tools, text="Dodaj izlazni račun", command=lambda: self.open_document("output")).pack(side="left")
        ttk.Button(tools, text="Budžet projekta", command=self.open_budget).pack(side="left", padx=4)
        ttk.Button(tools, text="PDV evidencija", command=self.open_vat_evidence).pack(side="left", padx=4)
        ttk.Button(tools, text="Izvoz za knjigovođu", command=self.open_accountant_export).pack(side="left", padx=4)
        ttk.Button(tools, text="Ponavljajuće fakture", command=self.open_recurring_invoices).pack(side="left", padx=4)
        ttk.Button(tools, text="Dokumenti projekta", command=self.open_archive).pack(side="left", padx=4)
        ttk.Button(tools, text="Osveži", command=self.refresh).pack(side="right")

        invoice_actions = ttk.LabelFrame(outer, text="Funkcije izabrane fakture", padding=(8, 5))
        invoice_actions.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(invoice_actions, text="Otvori fakturu", style="Primary.TButton", command=self.open_selected_invoice).pack(side="left")
        ttk.Button(invoice_actions, text="Uredi fakturu", command=self.edit_selected_invoice).pack(side="left", padx=6)
        ttk.Button(invoice_actions, text="Dodaj uplatu", command=self.add_payment_to_selected_invoice).pack(side="left", padx=6)
        ttk.Button(invoice_actions, text="Povraćaj", command=self.add_refund_to_selected_invoice).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Izdaj odobrenje", command=self.create_credit_note_for_selected_invoice).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="E-mail", command=self.send_selected_invoice).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Podsetnik", command=self.send_payment_reminder_to_selected_invoice).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="PDF / štampa", command=lambda: self.open_selected_invoice_export("pdf")).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Excel šablon", command=lambda: self.open_selected_invoice_export("xlsx")).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Prilozi fakture", command=self.open_selected_invoice_attachments).pack(side="left", padx=3)

        content = ttk.PanedWindow(outer, orient="vertical")
        content.grid(row=6, column=0, sticky="nsew")
        summary_frame = ttk.Frame(content, style="App.TFrame")
        content.add(summary_frame, weight=1)
        ledger_frame = ttk.Frame(content, style="App.TFrame")
        content.add(ledger_frame, weight=4)

        for column in range(5):
            summary_frame.columnconfigure(column, weight=1)
        self.metric_vars: dict[str, tk.StringVar] = {}
        cards = [
            ("Prihod bez PDV-a", "income_net"),
            ("Fakturisano sa PDV-om", "income_gross"),
            ("Naplaćeno", "paid_total"),
            ("Otvoreno za naplatu", "open_invoice_total"),
            ("Dospelo", "overdue_invoice_total"),
            ("Izlazni PDV", "income_vat"),
            ("Izdata odobrenja", "credit_note_gross"),
            ("Ulazni PDV", "expense_vat"),
            ("PDV za uplatu", "vat_difference"),
            ("Rad", "Rad"),
            ("Materijal", "Materijal"),
            ("Plate", "Plate"),
            ("Ostali troškovi", "Ostali troškovi"),
            ("Ukupan trošak", "expense_net"),
            ("Zarada bez PDV-a", "profit_net"),
            ("Planiran prihod", "planned_income_net"),
            ("Planiran trošak", "planned_expense_net"),
            ("Planirana zarada", "planned_profit_net"),
            ("Odstupanje zarade", "profit_variance_net"),
        ]
        for idx, (label, key) in enumerate(cards):
            card = ttk.Frame(summary_frame, style="Total.TFrame", padding=(10, 8))
            card.grid(row=idx // 5, column=idx % 5, sticky="ew", padx=3, pady=3)
            value_var = tk.StringVar(value="0,00 EUR")
            self.metric_vars[key] = value_var
            ttk.Label(card, text=label, style="TotalKey.TLabel").pack(anchor="w")
            value_style = "TotalDue.TLabel" if key in {"profit_net", "vat_difference", "overdue_invoice_total"} else "TotalValue.TLabel"
            ttk.Label(card, textvariable=value_var, style=value_style).pack(anchor="w", pady=(2, 0))

        ledger_frame.rowconfigure(1, weight=1)
        ledger_frame.columnconfigure(0, weight=1)
        ttk.Label(ledger_frame, text="Dokumenti projekta", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(10, 5))
        columns = ("type", "group", "date", "number", "partner", "description", "net", "vat", "gross")
        self.tree = ttk.Treeview(ledger_frame, columns=columns, show="headings")
        setup_treeview_tree(self.tree)
        for key, label, width, anchor in [
            ("type", "Tip", 135, "w"),
            ("group", "Grupa", 130, "w"),
            ("date", "Datum", 95, "w"),
            ("number", "Broj", 115, "w"),
            ("partner", "Dobavljač / kupac", 180, "w"),
            ("description", "Opis", 230, "w"),
            ("net", "Bez PDV-a", 115, "e"),
            ("vat", "PDV", 105, "e"),
            ("gross", "Ukupno", 115, "e"),
        ]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda event: self.edit_selected())
        scrollbar = ttk.Scrollbar(ledger_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=1, column=1, sticky="ns")

    def _selected_item(self) -> tuple[str, int] | None:
        selected = self.tree.selection()
        if not selected:
            return None
        source, raw_id = selected[0].split(":", 1)
        return source, int(raw_id)

    def open_document(self, document_type: str) -> None:
        allowed = {"owner", "administrator", "project_manager", "accountant", "operator"}
        action = "unos troška ili ulaznog računa"
        if document_type == "output":
            allowed = {"owner", "administrator", "project_manager", "accountant"}
            action = "unos izlaznog računa"
        if not self.app.require_team_permission(allowed, action, parent=self):
            return
        ProjectDocumentDialog(self, self.app, self.project_id, document_type=document_type, on_saved=self.refresh)

    def open_budget(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "project_manager", "accountant"}, "pregled budžeta projekta", parent=self):
            return
        if not self.app.require_plan_feature("project_budget", parent=self):
            return
        ProjectBudgetDialog(self, self.app, self.project_id, on_saved=self.refresh)

    def open_vat_evidence(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "project_manager", "accountant"}, "PDV evidencija", parent=self):
            return
        if not self.app.require_plan_feature("vat_evidence", parent=self):
            return
        ProjectVatEvidenceDialog(self, self.app, self.project_id)

    def open_accountant_export(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "project_manager", "accountant"}, "izvoz za knjigovođu", parent=self):
            return
        if not self.app.require_plan_feature("accountant_export", parent=self):
            return
        ProjectAccountantExportDialog(self, self.app, self.project_id)

    def open_recurring_invoices(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "upravljanje ponavljajućim fakturama",
            parent=self,
        ):
            return
        RecurringInvoiceTemplatesDialog(self, self.app, self.project_id, on_changed=self.refresh)

    def open_period_overview(self) -> None:
        ProjectPeriodOverviewDialog(self, self.app, self.project_id)

    def open_start_guide(self) -> None:
        ProjectStartGuideDialog(self, self)

    def open_reminders(self) -> None:
        ProjectRemindersDialog(self, self)

    def new_project_invoice(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "project_manager", "accountant"}, "izrada fakture", parent=self):
            return
        self.app.open_invoice_editor(project_id=self.project_id)
        self.refresh()

    def return_to_projects(self) -> None:
        """Return to the selected project instead of leaving another work window open."""
        try:
            self.app.tabs.select(self.app.projects_tab)
            tree = self.app.projects_tab.tree
            project_iid = str(self.project_id)
            if tree.exists(project_iid):
                tree.selection_set(project_iid)
                tree.focus(project_iid)
                tree.see(project_iid)
                self.app.projects_tab.on_select()
        except tk.TclError:
            pass
        self.destroy()

    def new_project_advance(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "project_manager", "accountant"}, "izdavanje avansa", parent=self):
            return
        try:
            self.db.project_advance_terms(self.project_id)
        except ValueError as exc:
            messagebox.showinfo("Ugovorni avans", str(exc), parent=self)
            return
        self.app.open_invoice_editor(project_id=self.project_id, initial_tab="details", invoice_kind="advance")
        self.refresh()

    def open_archive(self) -> None:
        ProjectArchiveDialog(self, self.app, self.project_id)

    def add_customer_to_project(self) -> None:
        """Let the guide add and link a customer without sending the user back through tabs."""
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "dodavanje kupca projektu",
            parent=self,
        ):
            return
        fields = [
            ("name", "Naziv firme", "entry", ""),
            ("eik", "EIK / BULSTAT", "entry", ""),
            ("vat_number", "PDV broj", "entry", ""),
            ("address", "Adresa", "entry", ""),
            ("contact_person", "Odgovorno lice", "entry", ""),
            ("phone", "Telefon", "entry", ""),
            ("email", "E-mail", "entry", ""),
            ("payment_term_days", "Rok plaćanja (dani)", "entry", str(DEFAULT_PAYMENT_TERM_DAYS)),
            ("note", "Napomena", "text", ""),
        ]

        def save_customer(payload: dict[str, Any]) -> bool:
            if not payload.get("name", "").strip():
                messagebox.showerror("Kupac", "Unesite naziv firme kupca.", parent=self)
                return False
            try:
                payload["payment_term_days"] = int(payload.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)
                customer_id = self.db.save_customer(payload)
                project_data = self.db.get_project(self.project_id)
                project_data.update({"id": self.project_id, "customer_id": customer_id})
                self.db.save_project(project_data)
            except (ValueError, TypeError) as exc:
                messagebox.showerror("Kupac", f"Kupca nije moguće sačuvati:\n{exc}", parent=self)
                return False
            self.project = self.db.get_project(self.project_id)
            self.app.refresh_all()
            self.refresh()
            return True

        EntityLineDialog(self, "Dodaj i poveži kupca", fields, save_customer)

    def add_project_payment(self) -> None:
        selected = self._selected_item()
        if selected and selected[0] == "invoice":
            PaymentDialog(self, self.db, selected[1], on_saved=self._refresh_after_payment_change)
            return
        open_rows = [
            row for row in self.db.list_invoices(project_id=self.project_id)
            if row.get("status_code") not in {"draft", "pending_approval", "approved", "cancelled", "paid"}
            and float(row.get("balance_total") or 0) > 0
        ]
        if not open_rows:
            messagebox.showinfo("Dodaj uplatu", "Ovaj projekat nema otvorenu izdatu fakturu za uplatu.", parent=self)
            return
        if len(open_rows) == 1:
            PaymentDialog(self, self.db, int(open_rows[0]["id"]), on_saved=self._refresh_after_payment_change)
            return
        ProjectPaymentPickerDialog(
            self,
            self.app,
            self.project_id,
            lambda invoice_id: PaymentDialog(self, self.db, invoice_id, on_saved=self._refresh_after_payment_change),
        )

    def _selected_invoice_id(self) -> int | None:
        selected = self._selected_item()
        if not selected:
            messagebox.showinfo("Faktura", "Izaberite fakturu iz pregleda projekta.")
            return None
        source, invoice_id = selected
        if source != "invoice":
            messagebox.showinfo("Faktura", "Ova funkcija važi za izdatu fakturu. Izaberite red sa tipom 'Izdana faktura'.")
            return None
        return invoice_id

    def open_selected_invoice(self) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            return
        self.app.open_invoice_editor(invoice_id)
        self.refresh()

    def edit_selected_invoice(self) -> None:
        """Open the selected invoice directly for changes from the project workspace."""
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "izmena fakture",
            parent=self,
        ):
            return
        self.open_selected_invoice()

    def add_payment_to_selected_invoice(self) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            return
        PaymentDialog(self, self.db, invoice_id, on_saved=self._refresh_after_payment_change)

    def add_refund_to_selected_invoice(self) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            return
        PaymentDialog(self, self.db, invoice_id, on_saved=self._refresh_after_payment_change, is_refund=True)

    def create_credit_note_for_selected_invoice(self) -> None:
        if not self.app.require_team_permission({"owner", "administrator", "project_manager", "accountant"}, "izdavanje odobrenja", parent=self):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Odobrenje", "Izaberite izdatu fakturu.", parent=self)
            return
        try:
            CreditNoteDialog(self, self.app, invoice_id, on_saved=self._refresh_after_payment_change)
        except ValueError as exc:
            messagebox.showerror("Odobrenje", str(exc), parent=self)

    def _refresh_after_payment_change(self) -> None:
        self.refresh()
        self.app.refresh_all()

    def send_selected_invoice(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "slanje fakture e-mailom",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            return
        SendEmailDialog(self, self.app, invoice_id, on_sent=self.refresh)

    def send_payment_reminder_to_selected_invoice(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "slanje podsetnika za plaćanje",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            return
        PaymentReminderDialog(self, self.app, invoice_id, on_sent=self._refresh_after_payment_change)

    def open_selected_invoice_export(self, format_name: str) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            return
        self.app.open_or_generate_invoice_output(invoice_id, format_name)

    def open_selected_invoice_attachments(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "upravljanje prilozima fakture",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            return
        self.app.open_invoice_editor(invoice_id, initial_tab="attachments")
        self.refresh()

    def edit_selected(self) -> None:
        selected = self._selected_item()
        if not selected:
            messagebox.showinfo("Finansije projekta", "Izaberite stavku iz pregleda.")
            return
        source, record_id = selected
        if source == "invoice":
            self.open_selected_invoice()
            return
        if source == "credit_note":
            self.app.open_or_generate_credit_note_output(record_id, "pdf")
            return
        ProjectDocumentDialog(self, self.app, self.project_id, document_id=record_id, on_saved=self.refresh)

    def delete_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "brisanje projektnog računa",
            parent=self,
        ):
            return
        selected = self._selected_item()
        if not selected:
            messagebox.showinfo("Finansije projekta", "Izaberite ručno unetu stavku za brisanje.")
            return
        source, record_id = selected
        if source == "invoice":
            messagebox.showinfo("Izlazna faktura", "Fakture se ne brišu iz projektnih finansija. Otvorite tab Fakture ako želite da je stornirate ili obrišete.")
            return
        if source == "credit_note":
            messagebox.showinfo("Odobrenje", "Izdato odobrenje se ne briše. Otvorite ga dvoklikom iz pregleda projekta.", parent=self)
            return
        if not messagebox.askyesno("Potvrda", "Obrisati izabrani ulazni ili izlazni račun?"):
            return
        self.db.delete_project_document(record_id)
        self.refresh()

    def refresh(self) -> None:
        self.project = self.db.get_project(self.project_id)
        summary = self.db.project_financial_summary(self.project_id)
        currency = self.app.company.get("default_currency") or DEFAULT_CURRENCY
        invoices = self.db.list_invoices(project_id=self.project_id)
        completed_steps = sum(
            (
                bool(self.project.get("customer_id")),
                True,
                summary.get("input_document_count", 0) > 0,
                summary.get("issued_invoice_count", 0) > 0,
                any(float(row.get("paid_total") or 0) > 0 for row in invoices),
            )
        )
        self.guide_var.set(tr("Početni vodič: {completed}/5 koraka završeno").format(completed=completed_steps))
        reminders = self.db.project_reminders(self.project_id)
        reminder_parts = []
        if reminders["due_soon"]:
            reminder_parts.append(tr("rokovi u 7 dana: {count}").format(count=len(reminders["due_soon"])))
        if reminders["overdue"]:
            reminder_parts.append(tr("dospeli kupci: {count}").format(count=len(reminders["overdue"])))
        if reminders["missing_pdf"]:
            reminder_parts.append(tr("ulazni računi bez PDF-a: {count}").format(count=len(reminders["missing_pdf"])))
        if reminders["over_budget"]:
            reminder_parts.append(tr("budžet prekoračen: {count}").format(count=len(reminders["over_budget"])))
        self.reminder_var.set(
            tr("Podsetnici: ") + (" | ".join(reminder_parts) if reminder_parts else tr("nema otvorenih obaveza"))
        )
        self.metric_vars["income_net"].set(fmt_money(summary["income_net"], currency))
        contract = summary.get("contract", {})
        if contract.get("net_amount", 0):
            self.contract_flow_var.set(
                "Ugovor bez PDV-a: {contract} | Fakturisano po ugovoru: {billed} | "
                "Preostalo: {remaining} | Realizacija: {progress:g}% | "
                "Avans: {advance_percent:g}% ({advance_planned}) | "
                "Izdato: {advance_issued} | Naplaćeno: {advance_paid} | Za naplatu: {advance_open}".format(
                    contract=fmt_money(contract.get("net_amount", 0), currency),
                    billed=fmt_money(contract.get("billed_net", 0), currency),
                    remaining=fmt_money(contract.get("remaining_net", 0), currency),
                    progress=money_round(contract.get("progress_percent", 0)),
                    advance_percent=decimal_from(contract.get("advance_percent", 0)),
                    advance_planned=fmt_money(contract.get("advance_planned_net", 0), currency),
                    advance_issued=fmt_money(contract.get("advance_issued_net", 0), currency),
                    advance_paid=fmt_money(contract.get("advance_paid_gross", 0), currency),
                    advance_open=fmt_money(contract.get("advance_open_gross", 0), currency),
                )
            )
        else:
            self.contract_flow_var.set(
                "Unesite vrednost ugovora bez PDV-a i procenat avansa u kartici Projekti, "
                "pa će OpsNest automatski voditi avans i realizaciju ugovora."
            )
        self.metric_vars["income_gross"].set(fmt_money(summary["income_gross"], currency))
        self.metric_vars["income_vat"].set(fmt_money(summary["income_vat"], currency))
        self.metric_vars["credit_note_gross"].set(fmt_money(summary["credit_note_gross"], currency))
        self.metric_vars["paid_total"].set(fmt_money(summary["paid_total"], currency))
        self.metric_vars["open_invoice_total"].set(fmt_money(summary["open_invoice_total"], currency))
        self.metric_vars["overdue_invoice_total"].set(fmt_money(summary["overdue_invoice_total"], currency))
        self.metric_vars["expense_vat"].set(fmt_money(summary["expense_vat"], currency))
        for group in PROJECT_COST_GROUPS:
            self.metric_vars[group].set(fmt_money(summary["cost_groups"][group]["net"], currency))
        self.metric_vars["expense_net"].set(fmt_money(summary["expense_net"], currency))
        self.metric_vars["profit_net"].set(fmt_money(summary["profit_net"], currency))
        self.metric_vars["vat_difference"].set(fmt_money(summary["vat_difference"], currency))
        budget = summary["budget"]
        if budget["is_configured"]:
            self.metric_vars["planned_income_net"].set(fmt_money(budget["planned_income_net"], currency))
            self.metric_vars["planned_expense_net"].set(fmt_money(budget["planned_expense_net"], currency))
            self.metric_vars["planned_profit_net"].set(fmt_money(budget["planned_profit_net"], currency))
            self.metric_vars["profit_variance_net"].set(fmt_money(budget["profit_variance_net"], currency))
        else:
            for key in ("planned_income_net", "planned_expense_net", "planned_profit_net", "profit_variance_net"):
                self.metric_vars[key].set(tr("Budžet nije unet"))

        for item in self.tree.get_children():
            self.tree.delete(item)
        ledger: list[dict[str, Any]] = []
        for row in self.db.list_invoices(project_id=self.project_id):
            if row.get("status_code") == "cancelled":
                continue
            workflow_status = str(row.get("status_code") or "draft")
            is_not_issued = workflow_status in {"draft", "pending_approval", "approved"}
            invoice_kind = normalize_invoice_kind(row.get("invoice_kind"))
            type_label = {
                "draft": tr("Nacrt fakture"),
                "pending_approval": tr("Faktura na proveri"),
                "approved": tr("Odobrena faktura"),
            }.get(workflow_status, tr("Izdana faktura"))
            if invoice_kind == "advance":
                type_label = {
                    "draft": tr("Nacrt avansa"),
                    "pending_approval": tr("Avans na proveri"),
                    "approved": tr("Odobren avans"),
                }.get(workflow_status, tr("Avansni račun"))
            elif invoice_kind == "final":
                type_label = {
                    "draft": tr("Nacrt završnog računa"),
                    "pending_approval": tr("Završni račun na proveri"),
                    "approved": tr("Odobren završni račun"),
                }.get(workflow_status, tr("Završni račun"))
            ledger.append({
                "iid": f'invoice:{row["id"]}',
                "date_sort": row.get("issue_date") or "",
                "type": type_label,
                "group": (
                    tr("Ugovorni avans — nije prihod")
                    if invoice_kind == "advance"
                    else tr("Nije u prihodu dok se ne izda")
                    if is_not_issued
                    else tr("Prihod projekta")
                ),
                "date": display_date(row.get("issue_date")),
                "number": row.get("invoice_number"),
                "partner": row.get("customer_name"),
                "description": row.get("project_name") or tr("Faktura projekta"),
                "net": row.get("tax_base", 0),
                "vat": row.get("vat_total", 0),
                "gross": row.get("gross_total", 0),
                "currency": row.get("currency") or currency,
            })
        for row in self.db.list_credit_notes(project_id=self.project_id):
            ledger.append({
                "iid": f'credit_note:{row["id"]}',
                "date_sort": row.get("issue_date") or "",
                "type": tr("Kreditno odobrenje"),
                "group": tr("Umanjenje prihoda"),
                "date": display_date(row.get("issue_date")),
                "number": row.get("credit_note_number"),
                "partner": row.get("customer_name"),
                "description": tr("Uz fakturu {invoice}: {reason}").format(
                    invoice=row.get("source_invoice_number") or "-",
                    reason=row.get("reason") or "",
                ),
                "net": -float(row.get("net_amount") or 0),
                "vat": -float(row.get("vat_amount") or 0),
                "gross": -float(row.get("gross_amount") or 0),
                "currency": row.get("currency") or currency,
            })
        for row in self.db.list_project_documents(self.project_id):
            is_input = row.get("document_type") == "input"
            ledger.append({
                "iid": f'document:{row["id"]}',
                "date_sort": row.get("document_date") or "",
                "type": tr("Ulazni račun") if is_input else tr("Izlazni račun"),
                "group": tr(row.get("cost_group") or ("Ostali troškovi" if is_input else "Ostali prihodi")),
                "date": display_date(row.get("document_date")),
                "number": row.get("document_no"),
                "partner": row.get("partner_name"),
                "description": row.get("description"),
                "net": row.get("net_amount", 0),
                "vat": row.get("vat_amount", 0),
                "gross": row.get("gross_amount", 0),
                "currency": row.get("currency") or currency,
            })
        for row in sorted(ledger, key=lambda entry: (entry["date_sort"], entry["iid"]), reverse=True):
            self.tree.insert(
                "",
                "end",
                iid=row["iid"],
                values=(
                    row["type"], row["group"], row["date"], row["number"], row["partner"], row["description"],
                    fmt_money(row["net"], row["currency"]), fmt_money(row["vat"], row["currency"]), fmt_money(row["gross"], row["currency"]),
                ),
                tags=(tree_row_tag(len(self.tree.get_children())),),
            )
        if self.on_changed:
            self.on_changed()


class InvoicesTab(ttk.Frame):
    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master, style="App.TFrame")
        self.app = app
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.customer_filter_var = tk.StringVar()
        self.project_filter_var = tk.StringVar()
        self.issue_from_var = tk.StringVar()
        self.issue_to_var = tk.StringVar()
        self.due_from_var = tk.StringVar()
        self.due_to_var = tk.StringVar()
        self.overdue_only_var = tk.BooleanVar(value=False)
        self.open_only_var = tk.BooleanVar(value=False)
        self.customer_map: dict[str, int] = {"": 0}
        self.project_map: dict[str, int] = {"": 0}
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        controls = ttk.LabelFrame(outer, text="Filteri i akcije", padding=10)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for col in range(12):
            controls.columnconfigure(col, weight=1 if col in {1, 3, 5, 7} else 0)

        ttk.Label(controls, text="Pretraga", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        search_entry = ttk.Entry(controls, textvariable=self.search_var, width=28, style="Modern.TEntry")
        search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=3)
        search_entry.bind("<Return>", lambda e: self.refresh())

        ttk.Label(controls, text="Status", style="Field.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 6), pady=3)
        self.status_combo = ttk.Combobox(controls, textvariable=self.status_var, values=["", *[localized_status_label(code) for code in STATUS_CODES]], width=20, state="readonly", style="Modern.TCombobox")
        self.status_combo.grid(row=0, column=3, sticky="ew", padx=(0, 14), pady=3)

        ttk.Label(controls, text="Kupac", style="Field.TLabel").grid(row=0, column=4, sticky="w", padx=(0, 6), pady=3)
        self.customer_combo = ttk.Combobox(controls, textvariable=self.customer_filter_var, values=[""], width=28, state="readonly", style="Modern.TCombobox")
        self.customer_combo.grid(row=0, column=5, sticky="ew", padx=(0, 14), pady=3)

        ttk.Label(controls, text="Projekat", style="Field.TLabel").grid(row=0, column=6, sticky="w", padx=(0, 6), pady=3)
        self.project_combo = ttk.Combobox(controls, textvariable=self.project_filter_var, values=[""], width=28, state="readonly", style="Modern.TCombobox")
        self.project_combo.grid(row=0, column=7, sticky="ew", padx=(0, 14), pady=3)

        ttk.Button(controls, text="Primeni", style="Primary.TButton", command=self.refresh).grid(row=0, column=8, sticky="ew", padx=(0, 6), pady=3)
        ttk.Button(controls, text="Reset", command=self.clear_filters).grid(row=0, column=9, sticky="ew", padx=(0, 6), pady=3)

        add_field(controls, 1, 0, "Izdato od", self.issue_from_var, width=14)
        add_field(controls, 1, 2, "Izdato do", self.issue_to_var, width=14)
        add_field(controls, 1, 4, "Rok od", self.due_from_var, width=14)
        add_field(controls, 1, 6, "Rok do", self.due_to_var, width=14)
        ttk.Checkbutton(controls, text="Samo dospele", variable=self.overdue_only_var).grid(row=1, column=8, sticky="w", padx=(0, 6), pady=3)
        ttk.Checkbutton(controls, text="Samo otvorene", variable=self.open_only_var).grid(row=1, column=9, sticky="w", padx=(0, 6), pady=3)

        invoice_actions = ttk.Frame(controls, style="App.TFrame")
        invoice_actions.grid(row=2, column=0, columnspan=12, sticky="w", pady=(8, 0))
        ttk.Button(invoice_actions, text="Nova faktura u projektu", style="Primary.TButton", command=self.new_invoice).pack(side="left", padx=(0, 6))
        ttk.Button(invoice_actions, text="Uredi", command=self.edit_selected).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Uplata", command=self.add_payment_selected).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="E-mail", command=self.send_email_selected).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Podsetnik", command=self.send_payment_reminder_selected).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="PDF / štampa", command=self.export_pdf_selected).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Excel šablon", command=self.export_xlsx_selected).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Prazan šablon", command=open_original_invoice_template).pack(side="left", padx=3)
        ttk.Button(invoice_actions, text="Obriši nacrt", command=self.delete_selected).pack(side="left", padx=3)
        audit_actions = ttk.Frame(controls, style="App.TFrame")
        audit_actions.grid(row=3, column=0, columnspan=12, sticky="w", pady=(5, 0))
        ttk.Button(audit_actions, text="Istorija", command=self.open_history_selected).pack(side="left", padx=(0, 6))
        ttk.Button(audit_actions, text="Povraćaj", command=self.add_payment_refund_selected).pack(side="left", padx=3)
        ttk.Button(audit_actions, text="Izdaj odobrenje", command=self.create_credit_note_selected).pack(side="left", padx=3)
        ttk.Button(audit_actions, text="Napravi ispravku", command=self.create_correction_selected).pack(side="left", padx=3)
        ttk.Button(audit_actions, text="Storniraj", command=self.cancel_selected).pack(side="left", padx=3)

        cols = ("number", "kind", "customer", "project", "issue", "due", "gross", "paid", "balance", "status")
        self.tree = ttk.Treeview(outer, columns=cols, show="headings")
        setup_treeview_tree(self.tree)
        headers = [
            ("number", "Broj", 110, "w"),
            ("kind", "Vrsta", 125, "w"),
            ("customer", "Kupac", 190, "w"),
            ("project", "Projekat", 190, "w"),
            ("issue", "Datum", 95, "w"),
            ("due", "Rok", 95, "w"),
            ("gross", "Ukupno", 120, "e"),
            ("paid", "Plaćeno", 120, "e"),
            ("balance", "Ostatak", 120, "e"),
            ("status", "Status", 130, "w"),
        ]
        for key, title, width, anchor in headers:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())
        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns")

    def _load_filter_values(self) -> None:
        selected_status = status_code_from_display(self.status_var.get())
        self.status_combo["values"] = ["", *[localized_status_label(code) for code in STATUS_CODES]]
        self.status_var.set(localized_status_label(selected_status) if selected_status else "")
        customers = self.app.db.list_customers()
        self.customer_map = {"": 0}
        customer_values = [""]
        for row in customers:
            display = f'{row["name"]} [{row["id"]}]'
            customer_values.append(display)
            self.customer_map[display] = row["id"]
        self.customer_combo["values"] = customer_values
        if self.customer_filter_var.get() not in self.customer_combo["values"]:
            self.customer_filter_var.set("")

        projects = self.app.db.list_projects()
        self.project_map = {"": 0}
        project_values = [""]
        for row in projects:
            display = f'{row["name"]} [{row["id"]}]'
            project_values.append(display)
            self.project_map[display] = row["id"]
        self.project_combo["values"] = project_values
        if self.project_filter_var.get() not in self.project_combo["values"]:
            self.project_filter_var.set("")

    def _selected_invoice_id(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def _selected_customer_id(self) -> int | None:
        value = self.customer_filter_var.get().strip()
        return self.customer_map.get(value) if value in self.customer_map else None

    def _selected_project_id(self) -> int | None:
        value = self.project_filter_var.get().strip()
        return self.project_map.get(value) if value in self.project_map else None

    def clear_filters(self) -> None:
        self.search_var.set("")
        self.status_var.set("")
        self.customer_filter_var.set("")
        self.project_filter_var.set("")
        self.issue_from_var.set("")
        self.issue_to_var.set("")
        self.due_from_var.set("")
        self.due_to_var.set("")
        self.overdue_only_var.set(False)
        self.open_only_var.set(False)
        self.refresh()

    def refresh(self) -> None:
        self._load_filter_values()
        for item in self.tree.get_children():
            self.tree.delete(item)
        status_code = status_code_from_display(self.status_var.get())
        for row in self.app.db.list_invoices(
            search=self.search_var.get().strip(),
            status_code=status_code,
            customer_id=self._selected_customer_id(),
            project_id=self._selected_project_id(),
            issue_from=self.issue_from_var.get().strip(),
            issue_to=self.issue_to_var.get().strip(),
            due_from=self.due_from_var.get().strip(),
            due_to=self.due_to_var.get().strip(),
            overdue_only=self.overdue_only_var.get(),
            open_only=self.open_only_var.get(),
        ):
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["invoice_number"],
                    INVOICE_KIND_LABELS.get(str(row.get("invoice_kind") or "standard"), INVOICE_KIND_LABELS["standard"]),
                    row["customer_name"],
                    row["project_name"],
                    display_date(row["issue_date"]),
                    display_date(row["due_date"]),
                    fmt_money(row["gross_total"], row["currency"]),
                    fmt_money(row["paid_total"], row["currency"]),
                    fmt_money(row["balance_total"], row["currency"]),
                    localized_status_label(row["status_code"]),
                ),
                tags=(tree_row_tag(len(self.tree.get_children())),),
            )

    def edit_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "izmena fakture",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Faktura", "Izaberite fakturu.")
            return
        self.app.open_invoice_editor(invoice_id)

    def new_invoice(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "izrada fakture",
            parent=self,
        ):
            return
        self.app.open_invoice_editor()

    def open_history_selected(self) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Istorija fakture", "Izaberite fakturu.")
            return
        InvoiceHistoryDialog(self, self.app.db, invoice_id)

    def create_correction_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "izrada ispravke fakture",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Ispravka fakture", "Izaberite fakturu.")
            return
        self.app.open_invoice_editor(correction_invoice_id=invoice_id)

    def cancel_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "storniranje fakture",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Storno fakture", "Izaberite fakturu.")
            return
        StornoInvoiceDialog(self, self.app, invoice_id, on_saved=self.app.refresh_all)

    def delete_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "brisanje nacrta fakture",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Faktura", "Izaberite fakturu.")
            return
        row = self.app.db.get_invoice(invoice_id)
        if row.get("status_code") not in {"draft", "pending_approval", "approved"}:
            messagebox.showinfo(
                "Faktura",
                "Samo nacrt ili faktura pre izdavanja može da se obriše. Izdatu fakturu stornirajte da bi broj i istorija ostali sačuvani.",
            )
            return
        if not messagebox.askyesno("Potvrda", f"Obrisati fakturu pre izdavanja {row.get('invoice_number')}?"):
            return
        try:
            self.app.db.delete_invoice(invoice_id)
        except ValueError as exc:
            messagebox.showerror("Faktura", str(exc))
            return
        self.refresh()
        self.app.refresh_all()

    def export_pdf_selected(self) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Faktura", "Izaberite fakturu.")
            return
        invoice = self.app.db.get_invoice(invoice_id)
        if str(invoice.get("status_code") or "draft") not in {"issued", "partial", "paid", "due"}:
            messagebox.showinfo("PDF / štampa", "PDF i štampa su dostupni nakon izdavanja. Za nacrt otvorite fakturu i koristite Pregled PDF / štampa.", parent=self)
            return
        try:
            task = self.app.prepare_invoice_output_task(invoice_id)
        except Exception as exc:
            messagebox.showerror("PDF / štampa", f"PDF iz originalnog šablona nije moguće napraviti:\n{exc}")
            return

        def export_complete(bundle: dict[str, Path]) -> None:
            open_path(bundle["pdf"])
            messagebox.showinfo("PDF / štampa", f"Pravi PDF je sačuvan i otvoren:\n{bundle['pdf']}", parent=self.app)

        self.app.run_pdf_export(
            title="Priprema PDF-a za štampu",
            task=task,
            on_success=export_complete,
        )

    def export_xlsx_selected(self) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Faktura", "Izaberite fakturu.")
            return
        invoice = self.app.db.get_invoice(invoice_id)
        if str(invoice.get("status_code") or "draft") not in {"issued", "partial", "paid", "due"}:
            messagebox.showinfo("Excel šablon", "Excel kopija je dostupna nakon izdavanja. Za nacrt otvorite fakturu i koristite Pregled Excel.", parent=self)
            return
        try:
            bundle = self.app.archive_invoice_outputs(invoice_id)
            open_path(bundle["xlsx"])
        except Exception as exc:
            messagebox.showerror("Excel šablon", f"Excel kopiju nije moguće napraviti:\n{exc}")
            return
        messagebox.showinfo("Excel šablon", f"Excel kopija je sačuvana i otvorena:\n{bundle['xlsx']}")

    def add_payment_selected(self) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Uplata", "Izaberite fakturu.")
            return
        PaymentDialog(self, self.app.db, invoice_id, on_saved=self.on_payment_saved)

    def add_payment_refund_selected(self) -> None:
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Povraćaj uplate", "Izaberite fakturu.")
            return
        PaymentDialog(self, self.app.db, invoice_id, on_saved=self.on_payment_saved, is_refund=True)

    def create_credit_note_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "izdavanje odobrenja",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Odobrenje", "Izaberite izdatu fakturu.", parent=self)
            return
        try:
            CreditNoteDialog(self, self.app, invoice_id, on_saved=self.on_payment_saved)
        except ValueError as exc:
            messagebox.showerror("Odobrenje", str(exc), parent=self)

    def send_email_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "slanje fakture e-mailom",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("E-mail", "Izaberite fakturu.")
            return
        SendEmailDialog(self, self.app, invoice_id, on_sent=self.on_payment_saved)

    def send_payment_reminder_selected(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "slanje podsetnika za plaćanje",
            parent=self,
        ):
            return
        invoice_id = self._selected_invoice_id()
        if not invoice_id:
            messagebox.showinfo("Podsetnik", "Izaberite fakturu.", parent=self)
            return
        PaymentReminderDialog(self, self.app, invoice_id, on_sent=self.on_payment_saved)

    def on_payment_saved(self) -> None:
        self.refresh()
        self.app.refresh_all()


class BackupTab(ttk.Frame):
    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master, style="App.TFrame")
        self.app = app
        self.root_var = tk.StringVar()
        self.db_var = tk.StringVar()
        self.backup_var = tk.StringVar()
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=14, pady=14)
        outer.columnconfigure(1, weight=1)
        add_field(outer, 0, 0, "Root folder", self.root_var, width=42, readonly=True)
        add_field(outer, 1, 0, "Database", self.db_var, width=42, readonly=True)
        add_field(outer, 2, 0, "Last backup", self.backup_var, width=42, readonly=True)
        ttk.Button(outer, text="Backup now", style="Primary.TButton", command=self.backup_now).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(outer, text="Restore backup", command=self.restore_backup).grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Button(outer, text="Open root folder", command=lambda: open_path(get_root_dir())).grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Button(outer, text="Open invoices folder", command=lambda: open_path(invoice_dir())).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Button(outer, text="Open backup folder", command=lambda: open_path(get_root_dir() / "Backup")).grid(row=4, column=1, sticky="w", pady=(8, 0))
        ttk.Label(
            outer,
            text="Automatski backup se pravi pri čuvanju faktura i uplata. Ovaj ekran služi i za ručni backup.",
            foreground=MUTED,
            background=BG,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(18, 0))

    def refresh(self) -> None:
        self.root_var.set(str(get_root_dir()))
        self.db_var.set(str(self.app.db.db_path))
        backups = sorted((get_root_dir() / "Backup").glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True) if (get_root_dir() / "Backup").exists() else []
        self.backup_var.set(str(backups[0]) if backups else "")

    def backup_now(self) -> None:
        path = self.app.db.backup_now()
        if path:
            self.backup_var.set(str(path))
            messagebox.showinfo("Backup", f"Backup je napravljen:\n{path}")
        else:
            messagebox.showwarning("Backup", "Backup nije napravljen.")

    def restore_backup(self) -> None:
        BackupRestoreDialog(self, self.app, on_restored=self.refresh)


class BackupRestoreDialog(tk.Toplevel):
    """Restore only validated local database snapshots and preserve a pre-restore copy."""

    def __init__(self, parent: tk.Widget, app: MainApp, *, on_restored: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.on_restored = on_restored
        self.rows: dict[str, dict[str, Any]] = {}
        self.title("Vraćanje bekapa")
        self.configure(background=BG)
        self.resizable(True, True)
        self._build()
        self.refresh()
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 820, 480)
        localize_widget_tree(self, app.ui_language)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(2, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Vraćanje bekapa", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text="Pre vraćanja OpsNest automatski pravi novu kopiju trenutne baze. Fakture, PDF-ovi i prilozi na disku se ne brišu.",
            style="Help.TLabel",
            wraplength=760,
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))
        self.tree = ttk.Treeview(outer, columns=("name", "created", "size"), show="headings", height=12)
        setup_treeview_tree(self.tree)
        for key, label, width, anchor in [
            ("name", "Backup datoteka", 420, "w"),
            ("created", "Napravljen", 180, "w"),
            ("size", "Veličina", 130, "e"),
        ]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.grid(row=2, column=0, sticky="nsew")
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="Vrati izabrani backup", style="Primary.TButton", command=self.restore_selected).pack(side="left")
        ttk.Button(buttons, text="Osveži", command=self.refresh).pack(side="left", padx=6)
        ttk.Button(buttons, text="Otvori Backup folder", command=lambda: open_path(get_root_dir() / "Backup")).pack(side="left")
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="right")

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.rows.clear()
        for index, row in enumerate(self.app.db.list_backups()):
            iid = f"backup:{index}"
            self.rows[iid] = row
            size_mb = float(row.get("size") or 0) / (1024 * 1024)
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(row.get("name") or "-", row.get("created_at") or "-", f"{size_mb:.2f} MB"),
                tags=(tree_row_tag(index),),
            )

    def restore_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Vraćanje bekapa", "Izaberite backup koji želite da vratite.", parent=self)
            return
        row = self.rows.get(selected[0])
        if not row:
            return
        if not messagebox.askyesno(
            "Potvrda vraćanja",
            "Vratiti izabrani backup baze?\n\nTrenutno stanje baze će prvo biti automatski sačuvano u Backup folderu.",
            parent=self,
        ):
            return
        try:
            self.app.db.restore_backup(str(row["path"]))
        except Exception as exc:
            messagebox.showerror("Vraćanje bekapa", f"Backup nije moguće vratiti:\n{exc}", parent=self)
            return
        self.app.refresh_all()
        if self.on_restored:
            self.on_restored()
        messagebox.showinfo("Vraćanje bekapa", "Backup je vraćen. Trenutna baza je pre vraćanja automatski sačuvana.", parent=self)
        self.destroy()


class EntityLineDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Widget,
        title: str,
        fields: list[tuple[str, str, str, Any]],
        on_save: Callable[[dict[str, Any]], None],
        initial: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(True, True)
        self.on_save = on_save
        self.vars: dict[str, tk.StringVar] = {}
        self.widgets: list[tk.Widget] = []
        self.configure(background=BG)
        shell = ttk.Frame(self, style="App.TFrame", padding=(14, 14, 14, 8))
        shell.pack(fill="both", expand=True)
        form_scroll = ScrollableFrame(shell)
        form_scroll.pack(fill="both", expand=True)
        frm = form_scroll.inner
        frm.columnconfigure(1, weight=1)
        for r, (key, label, kind, default) in enumerate(fields):
            value = str((initial or {}).get(key, default) or "")
            var = tk.StringVar(value=value)
            self.vars[key] = var
            kind_name = str(kind or "").lower()
            if kind_name in {"text", "multiline", "textarea"}:
                ttk.Label(frm, text=label).grid(row=r, column=0, sticky="nw", padx=(0, 6), pady=3)
                widget = tk.Text(
                    frm,
                    height=4,
                    wrap="word",
                    background="white",
                    foreground=TEXT,
                    insertbackground=TEXT,
                    relief="solid",
                    borderwidth=1,
                    highlightthickness=1,
                    highlightbackground=LINE,
                )
                widget.grid(row=r, column=1, sticky="nsew", padx=(0, 14), pady=3)
                if value:
                    widget.insert("1.0", value)
            else:
                widget = add_field(frm, r, 0, label, var, width=34)
            self.widgets.append(widget)
        # Actions remain visible even if a small display needs to scroll the fields.
        btns = ttk.Frame(shell, style="App.TFrame")
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Sačuvaj", style="Primary.TButton", command=self.save).pack(side="left")
        ttk.Button(btns, text="Otkaži", command=self.destroy).pack(side="right")
        multiline_count = sum(1 for _, _, kind, _ in fields if str(kind or "").lower() in {"text", "multiline", "textarea"})
        normal_count = len(fields) - multiline_count
        requested_height = 170 + normal_count * 42 + multiline_count * 110
        _, desktop_height = desktop_work_area(self)
        center_window(self, 700, min(max(requested_height, 470), max(470, desktop_height - 60)))
        self.transient(master.winfo_toplevel())
        self.grab_set()
        if self.widgets:
            self.widgets[0].focus_set()
        def save_handler(event: tk.Event) -> str:
            self.save()
            return "break"

        for idx, widget in enumerate(self.widgets):
            widget.bind("<Escape>", lambda e: self.destroy())
            widget.bind("<Control-Return>", save_handler)
            if isinstance(widget, tk.Text):
                continue
            if idx < len(self.widgets) - 1:
                widget.bind("<Return>", lambda e, nxt=self.widgets[idx + 1]: (nxt.focus_set(), "break")[1])
            else:
                widget.bind("<Return>", save_handler)

    def save(self) -> None:
        payload: dict[str, Any] = {}
        for key, var in self.vars.items():
            payload[key] = var.get().strip()
        for idx, widget in enumerate(self.widgets):
            if isinstance(widget, tk.Text):
                key = list(self.vars.keys())[idx]
                payload[key] = widget.get("1.0", "end").strip()
        try:
            result = self.on_save(payload)
        except Exception as exc:
            messagebox.showerror("Greška", f"Nije moguće sačuvati zapis:\n{exc}")
            return
        if result is False:
            return
        self.destroy()


class InvoiceHistoryDialog(tk.Toplevel):
    ACTION_LABELS = {
        "created": "Kreirana",
        "updated": "Izmenjena",
        "status_changed": "Promena statusa",
        "payment_added": "Dodana uplata",
        "refund_added": "Povraćaj uplate",
        "credit_note_issued": "Izdato formalno odobrenje",
        "payment_deleted": "Obrisana uplata",
        "cancelled": "Stornirana",
        "correction_draft_opened": "Priprema ispravke",
        "submitted_for_approval": "Poslata na proveru",
        "returned_for_revision": "Vraćena na doradu",
        "approved": "Odobrena za izdavanje",
        "issued_after_approval": "Izdata nakon odobrenja",
        "payment_reminder_sent": "Poslat podsetnik za plaćanje",
        "recurring_invoice_generated": "Kreiran ponavljajući nacrt",
    }

    def __init__(self, master: tk.Widget, db: Database, invoice_id: int) -> None:
        super().__init__(master)
        self.db = db
        self.invoice_id = invoice_id
        invoice = db.get_invoice(invoice_id)
        self.title(f"Istorija fakture {invoice.get('invoice_number') or ''}".strip())
        self.configure(background=BG)
        self.minsize(720, 360)
        self._build(invoice)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 900, 500)

    def _build(self, invoice: dict[str, Any]) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Istorija fakture", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text=f"{invoice.get('invoice_number') or '-'} | {invoice.get('customer_name') or '-'} | {localized_status_label(invoice.get('status_code') or 'draft')}",
            style="Help.TLabel",
        ).grid(row=0, column=0, sticky="e")

        table = ttk.Frame(outer, style="Panel.TFrame")
        table.grid(row=1, column=0, sticky="nsew", pady=(12, 10))
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        tree = ttk.Treeview(table, columns=("date", "action", "details"), show="headings")
        setup_treeview_tree(tree)
        tree.heading("date", text="Vreme")
        tree.heading("action", text="Akcija")
        tree.heading("details", text="Detalj")
        tree.column("date", width=165, anchor="w", stretch=False)
        tree.column("action", width=170, anchor="w", stretch=False)
        tree.column("details", width=520, anchor="w", stretch=True)
        scroll = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        rows = self.db.list_invoice_audit(self.invoice_id)
        if not rows:
            tree.insert("", "end", values=("", "", "Za starije fakture nema prethodno snimljene istorije."))
        for index, row in enumerate(rows):
            tree.insert(
                "",
                "end",
                values=(
                    str(row.get("created_at") or "").replace("T", " "),
                    tr(self.ACTION_LABELS.get(str(row.get("action_code") or ""), row.get("action_code") or "")),
                    row.get("details") or "",
                ),
                tags=(tree_row_tag(index),),
            )
        ttk.Button(outer, text="Zatvori", command=self.destroy).grid(row=2, column=0, sticky="e")
        localize_widget_tree(self, active_ui_language())


class EInvoiceConnectionDialog(tk.Toplevel):
    """Per-company e-invoice setup; government credentials are never stored."""

    def __init__(self, master: tk.Widget, company: dict[str, Any]) -> None:
        super().__init__(master)
        self.company = dict(company or {})
        self.provider = provider_for_country(self.company.get("country_code"))
        self.api_key_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.title("Poveži e-fakture")
        self.configure(background=BG)
        self.resizable(False, False)
        self._build()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 760, 390)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Poveži e-fakture", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        country = str(self.company.get("country_code") or "").strip().upper() or "nije izabrana"
        if self.provider is None:
            message = (
                f"Država firme: {country}. OpsNest može da pripremi lokalni UBL 2.1 nacrt, "
                "ali direktna državna veza biće dodata kada postoji provereni konektor za tu državu. "
                "Svaka firma će tada unositi sopstveni ključ, sertifikat ili drugi metod prijave."
            )
            ttk.Label(outer, text=message, style="Help.TLabel", wraplength=700, justify="left").grid(row=1, column=0, sticky="w", pady=(10, 12))
            ttk.Button(outer, text="Zatvori", command=self.destroy).grid(row=2, column=0, sticky="e", pady=(18, 0))
            localize_widget_tree(self, active_ui_language())
            return

        if self.provider.country_code == "BG":
            ttk.Label(outer, text="Bugarska — EN 16931 / B2G priprema", style="CardTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 2))
            ttk.Label(
                outer,
                text=(
                    "Za Bugarsku OpsNest ne traži srpski SEF ključ i ne šalje dokument u CAIS EPP. "
                    "Kada kupac ili javni naručilac zahteva strukturiranu fakturu, prvo izdate fakturu, "
                    "pokrenete E-faktura proveru i zatim sačuvate UBL 2.1 nacrt za tehnički pregled.\n\n"
                    "Dokument ostaje lokalni nacrt: nije CAIS EPP predaja niti sertifikovana EN 16931 validacija."
                ),
                style="Help.TLabel", wraplength=700, justify="left",
            ).grid(row=2, column=0, sticky="w", pady=(0, 12))
            ttk.Button(outer, text="Zatvori", command=self.destroy).grid(row=3, column=0, sticky="e", pady=(18, 0))
            localize_widget_tree(self, active_ui_language())
            return

        if not self.provider.supports_demo_connection:
            selected_route = einvoice_route_code_from_label(self.company.get("einvoice_route"))
            route_copy = {
                "automatic": "OpsNest će koristiti preporučeni tok za državu firme.",
                "structured_ubl": "Izabran je strukturirani UBL / EN 16931 dokument za tehnički pregled i razmenu.",
                "external_portal": "Izabran je spoljni portal ili provajder; OpsNest će pripremiti podatke i UBL nacrt, a slanje se obavlja u tom servisu.",
            }[selected_route]
            ttk.Label(outer, text=self.provider.display_name, style="CardTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 2))
            ttk.Label(
                outer,
                text=(
                    f"{route_copy}\n\n"
                    "Direktna državna veza za ovu državu još nije aktivirana. Koristite E-faktura proveru i UBL 2.1 nacrt, "
                    "a državne ili provajderske pristupne podatke unosite tek kada postoji provereni konektor za tu državu."
                ),
                style="Help.TLabel", wraplength=700, justify="left",
            ).grid(row=2, column=0, sticky="w", pady=(0, 12))
            ttk.Button(outer, text="Zatvori", command=self.destroy).grid(row=3, column=0, sticky="e", pady=(18, 0))
            localize_widget_tree(self, active_ui_language())
            return

        ttk.Label(outer, text=self.provider.display_name, style="CardTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 2))
        ttk.Label(
            outer,
            text=(
                "Povezivanje važi samo za trenutno otvorenu firmu. Ključ izdaje državni sistem toj firmi "
                "i ne sme se deliti sa drugim firmama."
            ),
            style="Help.TLabel", wraplength=700, justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(0, 12))
        form = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        form.grid(row=3, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="SEF demo API ključ", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Entry(form, textvariable=self.api_key_var, show="*", width=52).grid(row=0, column=1, sticky="ew")
        ttk.Label(
            form,
            text="Ključ ostaje samo u memoriji ovog prozora i šalje se direktno na SEF demo radi provere veze.",
            style="Help.TLabel", wraplength=590, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(outer, textvariable=self.status_var, style="Help.TLabel", wraplength=700).grid(row=4, column=0, sticky="w", pady=(12, 0))
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=5, column=0, sticky="e", pady=(16, 0))
        self.test_button = ttk.Button(buttons, text="Testiraj demo vezu", style="Primary.TButton", command=self.test_connection)
        self.test_button.pack(side="left", padx=(0, 7))
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="left")
        localize_widget_tree(self, active_ui_language())

    def test_connection(self) -> None:
        if self.provider is None:
            return
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("E-faktura povezivanje", "Unesite API ključ koji je firma generisala u SEF demo nalogu.", parent=self)
            return
        if not messagebox.askyesno(
            "Potvrda demo provere",
            "OpsNest će poslati samo API ključ direktno na SEF demo da pročita verziju sistema. "
            "Nijedna faktura, XML ili podatak firme neće biti poslat. Nastaviti?",
            parent=self,
        ):
            return
        self.test_button.configure(state="disabled")
        self.status_var.set("Proveravam SEF demo vezu…")

        def worker() -> None:
            try:
                version = get_sef_version(api_key, environment="demo")
            except SefApiError as exc:
                self.after(0, lambda: self._finish_test(error=str(exc)))
                return
            self.after(0, lambda: self._finish_test(version=version))

        threading.Thread(target=worker, name="opsnest-einvoice-demo-check", daemon=True).start()

    def _finish_test(self, *, version: str = "", error: str = "") -> None:
        if not self.winfo_exists():
            return
        self.test_button.configure(state="normal")
        if error:
            self.status_var.set("Demo veza nije potvrđena.")
            messagebox.showwarning("E-faktura povezivanje", error, parent=self)
            return
        self.api_key_var.set("")
        self.status_var.set(f"Demo veza je potvrđena — SEF verzija {version}. Ključ nije sačuvan.")
        messagebox.showinfo(
            "E-faktura povezivanje",
            "Demo veza je potvrđena. Ključ nije sačuvan i nijedan dokument nije poslat. "
            "Sledeći korak je validacija jednostavne test fakture u demo okruženju.",
            parent=self,
        )


class EInvoiceOutboxDialog(tk.Toplevel):
    """Read-only view of locally prepared structured invoice documents."""

    STATUS_LABELS = {
        "review_only": "Samo za pregled",
        "ready_to_submit": "Spremno za slanje",
        "submitted": "Poslato",
        "accepted": "Prihvaćeno",
        "rejected": "Odbijeno",
        "error": "Greška",
    }

    def __init__(self, master: tk.Widget, db: Database, invoice_id: int) -> None:
        super().__init__(master)
        self.db = db
        self.invoice_id = invoice_id
        self.rows: dict[str, dict[str, Any]] = {}
        invoice = db.get_invoice(invoice_id)
        self.title(f"E-faktura outbox {invoice.get('invoice_number') or ''}".strip())
        self.configure(background=BG)
        self.minsize(820, 360)
        self._build(invoice)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 980, 500)

    def _build(self, invoice: dict[str, Any]) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="E-faktura outbox", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text=f"{invoice.get('invoice_number') or '-'} | lokalni dokumenti se ne šalju automatski",
            style="Help.TLabel",
        ).grid(row=0, column=0, sticky="e")
        table = ttk.Frame(outer, style="Panel.TFrame")
        table.grid(row=1, column=0, sticky="nsew", pady=(12, 10))
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("date", "provider", "country", "format", "status", "hash"), show="headings")
        setup_treeview_tree(self.tree)
        for key, label, width in (
            ("date", "Ažurirano", 155),
            ("provider", "Konektor", 150),
            ("country", "Država", 70),
            ("format", "Format", 130),
            ("status", "Status", 145),
            ("hash", "Kontrolni zbir", 180),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=key in {"provider", "hash"})
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        documents = self.db.list_einvoice_documents(self.invoice_id)
        if not documents:
            self.tree.insert("", "end", values=("", "", "", "", "", "UBL nacrt još nije napravljen."))
        for index, row in enumerate(documents):
            item_id = self.tree.insert(
                "",
                "end",
                values=(
                    str(row.get("updated_at") or "").replace("T", " "),
                    row.get("provider_code") or "generic-ubl",
                    row.get("country_code") or "-",
                    row.get("format_code") or "-",
                    self.STATUS_LABELS.get(str(row.get("status_code") or ""), row.get("status_code") or "-"),
                    str(row.get("document_hash") or "")[:16],
                ),
                tags=(tree_row_tag(index),),
            )
            self.rows[item_id] = row
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=2, column=0, sticky="e")
        ttk.Button(buttons, text="Otvori dokument", command=self.open_selected).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="left")
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())
        localize_widget_tree(self, active_ui_language())

    def open_selected(self) -> None:
        selected = self.tree.selection()
        if not selected or selected[0] not in self.rows:
            messagebox.showinfo("E-faktura outbox", "Izaberite dokument iz liste.", parent=self)
            return
        path = Path(str(self.rows[selected[0]].get("document_path") or ""))
        if not path.is_file():
            messagebox.showwarning("E-faktura outbox", "Lokalni XML dokument više nije pronađen na sačuvanoj putanji.", parent=self)
            return
        open_path(path)


class FinancialAdvisorDialog(tk.Toplevel):
    """Present local insights and an opt-in, Pro-only aggregate AI review."""

    PRIORITY_LABELS = {"high": "Prioritet", "medium": "Pažnja", "info": "Planiranje", "good": "Stabilno"}

    def __init__(self, master: tk.Widget, app: MainApp, stats: dict[str, Any], period_caption: str) -> None:
        super().__init__(master)
        self.app = app
        self.stats = stats
        self.title("Finansijski savetnik")
        self.configure(background=BG)
        self.minsize(800, 560)
        self._build(stats, period_caption)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 980, 700)

    def _build(self, stats: dict[str, Any], period_caption: str) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=14)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(2, weight=1)
        outer.rowconfigure(3, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text="Finansijski savetnik", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text=period_caption, style="Help.TLabel").grid(row=0, column=0, sticky="e")
        ttk.Label(
            outer,
            text="Lokalna analiza koristi samo zbirne podatke iz dashboarda. Pro AI analiza se pokreće samo na Vaš zahtev i šalje isključivo numerički zbir — bez faktura, priloga, kupaca i projekata.",
            style="Help.TLabel",
            wraplength=900,
        ).grid(row=1, column=0, sticky="w", pady=(6, 10))
        table = ttk.Frame(outer, style="Panel.TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        tree = ttk.Treeview(table, columns=("priority", "title", "observation", "action"), show="headings")
        setup_treeview_tree(tree)
        for key, label, width in (
            ("priority", "Nivo", 95),
            ("title", "Tema", 220),
            ("observation", "Nalaz", 280),
            ("action", "Predlog", 350),
        ):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w", stretch=key != "priority")
        scroll = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for index, insight in enumerate(financial_insights(stats)):
            tree.insert(
                "",
                "end",
                values=(
                    self.PRIORITY_LABELS.get(insight.priority, insight.priority),
                    insight.title,
                    insight.observation,
                    insight.suggested_action,
                ),
                tags=(tree_row_tag(index),),
            )
        ai_panel = ttk.LabelFrame(outer, text="OpsNest AI finansijski savetnik · opcioni dodatak", style="TLabelframe", padding=10)
        ai_panel.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        ai_panel.rowconfigure(1, weight=1)
        ai_panel.columnconfigure(0, weight=1)
        self.ai_status_var = tk.StringVar(value="AI dodaci: Starter 100, Business 200 ili Pro 300 saveta mesečno. Pokreće se samo kada ga zatražite.")
        ttk.Label(ai_panel, textvariable=self.ai_status_var, style="Help.TLabel", wraplength=860).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.ai_text = tk.Text(
            ai_panel, height=8, wrap="word", background="white", foreground=TEXT,
            relief="solid", borderwidth=1, highlightthickness=1, highlightbackground=LINE, state="disabled",
        )
        self.ai_text.grid(row=1, column=0, sticky="nsew")
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=4, column=0, sticky="e", pady=(10, 0))
        self.ai_button = ttk.Button(buttons, text="Zatraži AI savet", style="Primary.TButton", command=self.request_ai_advice)
        self.ai_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Zatvori", command=self.destroy).pack(side="left")
        localize_widget_tree(self, active_ui_language())

    def _show_ai_advice(self, text: str) -> None:
        self.ai_text.configure(state="normal")
        self.ai_text.delete("1.0", "end")
        self.ai_text.insert("1.0", text)
        self.ai_text.configure(state="disabled")

    def request_ai_advice(self) -> None:
        connection = self.app.db.cloud_connection()
        subscription = self.app.db.get_subscription()
        workspace_id = str(subscription.get("workspace_id") or "").strip()
        workspace_token = str(connection.get("workspace_token") or "").strip()
        if not workspace_id or not workspace_token:
            messagebox.showinfo("OpsNest AI savetnik", "Za AI savet prvo aktivirajte online OpsNest nalog iz centra za pakete.", parent=self)
            return
        language = active_ui_language()
        if language not in {"sr", "en", "de"}:
            language = "en"
        company_currency = str(self.app.company.get("default_currency") or DEFAULT_CURRENCY).upper()
        company_finance = self.app.db.company_financial_summary()
        currency_pnl = dict(company_finance.get("currencies", {}).get(company_currency) or {})
        forecast = self.app.db.cash_flow_forecast(days=90)
        currency_flow = dict(forecast.get("currencies", {}).get(company_currency) or {})
        open_payables = sum(
            (money_round(row.get("balance_amount")) for row in self.app.db.list_vendor_bills(include_paid=False) if str(row.get("currency") or "").upper() == company_currency),
            Decimal("0"),
        )
        opening_cash = sum(
            (money_round(row.get("opening_balance")) for row in self.app.db.list_cash_accounts() if str(row.get("currency") or "").upper() == company_currency),
            Decimal("0"),
        )
        summary = ai_financial_summary(
            self.stats,
            currency=company_currency,
            business_profile=str(self.app.company.get("business_profile") or "general"),
            language=language,
            finance={
                "expense_total": currency_pnl.get("expense_net", 0),
                "open_payables_total": open_payables,
                "cash_opening_total": opening_cash,
                "cash_forecast_closing_total": currency_flow.get("closing_balance", 0),
                "cash_flow_horizon_days": 90,
            },
        )
        self.ai_button.configure(state="disabled")
        self.ai_status_var.set("OpsNest AI priprema savet iz anonimnog zbirnog pregleda…")
        results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                client = OpsNestCloudClient(connection.get("api_url") or OPSNEST_CLOUD_API_URL)
                results.put((True, client.financial_advice(workspace_id=workspace_id, workspace_token=workspace_token, summary=summary)))
            except Exception as exc:
                results.put((False, exc))

        def finish() -> None:
            try:
                ok, result = results.get_nowait()
            except queue.Empty:
                if self.winfo_exists():
                    self.after(80, finish)
                return
            if not self.winfo_exists():
                return
            self.ai_button.configure(state="normal")
            if not ok:
                self.ai_status_var.set("AI savet trenutno nije dostupan.")
                messagebox.showerror("OpsNest AI savetnik", str(result), parent=self)
                return
            advice = str(result.get("advice") or "").strip()
            remaining = result.get("requests_remaining")
            remaining_text = f" Preostalo ovog obračunskog perioda: {remaining}." if remaining is not None else ""
            self.ai_status_var.set("AI savet je generisan iz anonimnog zbirnog pregleda. Potvrdite poreske i računovodstvene odluke sa stručnim licem." + remaining_text)
            self._show_ai_advice(advice or "AI savet nije vraćen. Pokušajte ponovo kasnije.")

        threading.Thread(target=worker, name="opsnest-ai-financial-advice", daemon=True).start()
        self.after(80, finish)


class StornoInvoiceDialog(tk.Toplevel):
    def __init__(self, master: tk.Widget, app: MainApp, invoice_id: int, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(master)
        self.app = app
        self.invoice_id = invoice_id
        self.on_saved = on_saved
        self.invoice = app.db.get_invoice(invoice_id)
        self.title("Storno fakture")
        self.configure(background=BG)
        self._build()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 660, 420)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="Storniranje izdate fakture", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text="Storno zadržava originalni broj, PDF i istoriju, ali fakturu isključuje iz prihoda projekta.",
            style="Help.TLabel",
            wraplength=600,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 12))
        rows = [
            ("Broj fakture", self.invoice.get("invoice_number") or "-"),
            ("Kupac", self.invoice.get("customer_name") or "-"),
            ("Ukupno", fmt_money(self.invoice.get("gross_total") or 0, self.invoice.get("currency") or DEFAULT_CURRENCY)),
            ("Evidentirano plaćanje", fmt_money(self.invoice.get("paid_total") or 0, self.invoice.get("currency") or DEFAULT_CURRENCY)),
        ]
        for row, (label, value) in enumerate(rows, start=2):
            ttk.Label(outer, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 14), pady=3)
            ttk.Label(outer, text=value, style="CardTitle.TLabel").grid(row=row, column=1, sticky="w", pady=3)
        ttk.Label(outer, text="Razlog storna", style="Field.TLabel").grid(row=6, column=0, sticky="nw", padx=(0, 14), pady=(12, 3))
        self.reason_text = tk.Text(
            outer,
            height=5,
            wrap="word",
            background="white",
            foreground=TEXT,
            insertbackground=TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
        )
        self.reason_text.grid(row=6, column=1, sticky="ew", pady=(12, 3))
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Storniraj fakturu", style="Primary.TButton", command=self.save).pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")
        self.reason_text.focus_set()

    def save(self) -> None:
        reason = self.reason_text.get("1.0", "end").strip()
        if not messagebox.askyesno(
            "Potvrda storna",
            f"Stornirati fakturu {self.invoice.get('invoice_number') or ''}?\n\nOva radnja čuva dokument i broj fakture, ali je više ne računa kao prihod.",
            parent=self,
        ):
            return
        try:
            self.app.db.cancel_invoice(self.invoice_id, reason)
        except ValueError as exc:
            messagebox.showerror("Storno fakture", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.app.refresh_all()
        messagebox.showinfo("Storno fakture", "Faktura je stornirana i ostaje sačuvana u istoriji.", parent=self)
        self.destroy()


class PaymentDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Widget,
        db: Database,
        invoice_id: int,
        on_saved: Callable[[], None] | None = None,
        *,
        is_refund: bool = False,
    ) -> None:
        super().__init__(master)
        self.db = db
        self.invoice_id = invoice_id
        self.on_saved = on_saved
        self.is_refund = is_refund
        self.invoice = db.get_invoice(invoice_id)
        self.title("Povraćaj / odobrenje uplate" if is_refund else "Dodaj uplatu")
        self.configure(background=BG)
        self.vars = {
            "payment_date": tk.StringVar(value=date.today().strftime("%d.%m.%Y")),
            "amount": tk.StringVar(),
            "method": tk.StringVar(value=payment_method_default()),
            "note": tk.StringVar(),
        }
        frm = ttk.Frame(self, style="App.TFrame", padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        if is_refund:
            ttk.Label(frm, text="Povraćaj / odobrenje uplate", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
            ttk.Label(
                frm,
                text=f"Možete vratiti najviše {fmt_money(self.invoice.get('paid_total') or 0, self.invoice.get('currency') or DEFAULT_CURRENCY)}. Originalna uplata ostaje u istoriji.",
                style="Help.TLabel",
                wraplength=380,
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        first_row = 2 if is_refund else 0
        add_field(frm, first_row, 0, "Datum", self.vars["payment_date"], width=18)
        amount_entry = add_field(frm, first_row + 1, 0, "Iznos povraćaja" if is_refund else "Iznos", self.vars["amount"], width=18)
        add_combo(frm, first_row + 2, 0, "Način", self.vars["method"], list(PAYMENT_METHOD_OPTIONS))
        ttk.Label(frm, text="Razlog" if is_refund else "Napomena").grid(row=first_row + 3, column=0, sticky="nw", padx=(0, 6), pady=3)
        self.note_text = tk.Text(
            frm,
            height=4,
            wrap="word",
            background="white",
            foreground=TEXT,
            insertbackground=TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
        )
        self.note_text.grid(row=first_row + 3, column=1, sticky="nsew", padx=(0, 14), pady=3)
        btns = ttk.Frame(frm, style="App.TFrame")
        btns.grid(row=first_row + 4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="Evidentiraj povraćaj" if is_refund else "Sačuvaj", style="Primary.TButton", command=self.save).pack(side="left")
        ttk.Button(btns, text="Otkaži", command=self.destroy).pack(side="right")
        center_window(self, 470, 390 if is_refund else 300)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        amount_entry.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: (self.save(), "break")[1])

    def save(self) -> None:
        try:
            amount = float(self.vars["amount"].get())
        except ValueError:
            messagebox.showerror("Greška", "Unesite ispravan iznos.")
            return
        payment_date = self.vars["payment_date"].get().strip()
        note = self.note_text.get("1.0", "end").strip()
        if self.is_refund and not note:
            messagebox.showerror("Povraćaj uplate", "Unesite razlog povraćaja ili odobrenja.", parent=self)
            return
        if self.is_refund and not messagebox.askyesno(
            "Potvrda povraćaja",
            f"Evidentirati povraćaj {fmt_money(amount, self.invoice.get('currency') or DEFAULT_CURRENCY)}?\n\n"
            "Originalna uplata se ne briše, već ostaje u istoriji fakture.",
            parent=self,
        ):
            return
        try:
            if self.is_refund:
                self.db.add_payment_refund(self.invoice_id, payment_date, amount, self.vars["method"].get().strip(), note)
            else:
                self.db.add_payment(self.invoice_id, payment_date, amount, self.vars["method"].get().strip(), note)
        except ValueError as exc:
            messagebox.showerror("Povraćaj uplate" if self.is_refund else "Uplata", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.destroy()


class CreditNoteDialog(tk.Toplevel):
    """Issue an immutable, project-owned credit note after a recorded refund."""

    def __init__(self, master: tk.Widget, app: MainApp, invoice_id: int, on_saved: Callable[[], None] | None = None) -> None:
        self.app = app
        self.db = app.db
        self.invoice_id = invoice_id
        self.on_saved = on_saved
        self.info = self.db.credit_note_draft_info(invoice_id)
        self.invoice = self.info["invoice"]
        self.credit_note_id: int | None = None
        self.amount_var = tk.StringVar(value=f"{self.info['available_gross']:.2f}".replace(".", ","))
        self.issue_date_var = tk.StringVar(value=date.today().strftime("%d.%m.%Y"))
        self.amount_summary_var = tk.StringVar()
        self.status_var = tk.StringVar(value="")
        super().__init__(master)
        self.title("Izdaj formalno odobrenje")
        self.configure(background=BG)
        self._build()
        self._refresh_amount_summary()
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        center_window(self, 760, 530)

    @staticmethod
    def _amount_value(value: str) -> float:
        raw = str(value or "").strip().replace(" ", "")
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        return float(raw)

    def _build(self) -> None:
        outer = ttk.Frame(self, style="App.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="Formalno kreditno odobrenje", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            outer,
            text="Broj dokumenta se dodeljuje samo jednom. Iznos ne može biti veći od već evidentiranog povraćaja uplate.",
            style="Help.TLabel",
            wraplength=710,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 12))

        source_rows = [
            ("Izvorna faktura", self.invoice.get("invoice_number") or "-"),
            ("Kupac", self.invoice.get("customer_name") or "-"),
            ("Projekat", self.invoice.get("project_name") or "-"),
            ("Ukupno povraćeno", fmt_money(self.info["refunded_total"], DEFAULT_CURRENCY)),
            ("Već izdato odobrenje", fmt_money(self.info["credited_total"], DEFAULT_CURRENCY)),
            ("Raspoloživo za odobrenje", fmt_money(self.info["available_gross"], DEFAULT_CURRENCY)),
        ]
        for row, (label, value) in enumerate(source_rows, start=2):
            ttk.Label(outer, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 14), pady=2)
            ttk.Label(outer, text=value, style="CardTitle.TLabel").grid(row=row, column=1, sticky="w", pady=2)

        ttk.Separator(outer).grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 10))
        add_field(outer, 9, 0, "Datum odobrenja", self.issue_date_var, width=20)
        amount_entry = add_field(outer, 10, 0, "Ukupno odobrenje (EUR)", self.amount_var, width=20)
        amount_entry.bind("<KeyRelease>", lambda _event: self._refresh_amount_summary())
        amount_entry.bind("<FocusOut>", lambda _event: self._refresh_amount_summary())
        ttk.Label(outer, textvariable=self.amount_summary_var, style="Help.TLabel", wraplength=500).grid(
            row=11, column=1, sticky="w", pady=(1, 8)
        )
        ttk.Label(outer, text="Razlog korekcije", style="Field.TLabel").grid(row=12, column=0, sticky="nw", padx=(0, 14), pady=(4, 2))
        self.reason_text = tk.Text(
            outer,
            height=4,
            wrap="word",
            background="white",
            foreground=TEXT,
            insertbackground=TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
        )
        self.reason_text.grid(row=12, column=1, sticky="ew", pady=(4, 2))
        ttk.Label(outer, textvariable=self.status_var, style="Help.TLabel", wraplength=700).grid(
            row=13, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        buttons = ttk.Frame(outer, style="App.TFrame")
        buttons.grid(row=14, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.issue_button = ttk.Button(buttons, text="Izdaj i otvori PDF", style="Primary.TButton", command=self.issue_or_retry)
        self.issue_button.pack(side="left")
        ttk.Button(buttons, text="Otkaži", command=self.destroy).pack(side="right")
        self.reason_text.focus_set()

    def _refresh_amount_summary(self) -> None:
        try:
            amounts = self.db.preview_credit_note_amounts(self.invoice_id, self._amount_value(self.amount_var.get()))
        except (ValueError, TypeError):
            self.amount_summary_var.set("Unesite iznos do raspoloživog povraćaja.")
            return
        self.amount_summary_var.set(
            f"Osnovica: {fmt_money(amounts['net_amount'], DEFAULT_CURRENCY)}    "
            f"PDV: {fmt_money(amounts['vat_amount'], DEFAULT_CURRENCY)}    "
            f"Ukupno: {fmt_money(amounts['gross_amount'], DEFAULT_CURRENCY)}"
        )

    def issue_or_retry(self) -> None:
        if self.credit_note_id is not None:
            self._generate_outputs()
            return
        reason = self.reason_text.get("1.0", "end").strip()
        try:
            gross_amount = self._amount_value(self.amount_var.get())
            amounts = self.db.preview_credit_note_amounts(self.invoice_id, gross_amount)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Odobrenje", str(exc) or "Unesite ispravan iznos.", parent=self)
            return
        if not reason:
            messagebox.showerror("Odobrenje", "Unesite razlog korekcije.", parent=self)
            return
        if not messagebox.askyesno(
            "Potvrda izdavanja",
            f"Izdati formalno odobrenje uz fakturu {self.invoice.get('invoice_number') or ''}?\n\n"
            f"Ukupno: {fmt_money(amounts['gross_amount'], DEFAULT_CURRENCY)}\n"
            "Broj odobrenja i dokument ostaju trajno sačuvani u projektu.",
            parent=self,
        ):
            return
        try:
            self.credit_note_id = self.db.create_credit_note(self.invoice_id, self.issue_date_var.get(), gross_amount, reason)
        except ValueError as exc:
            messagebox.showerror("Odobrenje", str(exc), parent=self)
            return
        note = self.db.get_credit_note(self.credit_note_id)
        self.status_var.set(
            f"Odobrenje {note.get('credit_note_number') or ''} je izdato. Pripremaju se PDF i Excel kopija u folderu projekta."
        )
        self.issue_button.configure(text="Ponovo napravi PDF/Excel")
        self._generate_outputs()

    def _generate_outputs(self) -> None:
        if self.credit_note_id is None:
            return
        try:
            task = self.app.prepare_credit_note_output_task(self.credit_note_id)
        except Exception as exc:
            messagebox.showerror("Odobrenje", f"PDF i Excel nije moguće pripremiti:\n{exc}", parent=self)
            return

        def complete(bundle: dict[str, Path]) -> None:
            open_path(bundle["pdf"])
            if self.on_saved:
                self.on_saved()
            self.app.refresh_all()
            messagebox.showinfo(
                "Odobrenje je izdato",
                f"PDF i Excel kopija su sačuvane u projektu:\n{bundle['pdf']}",
                parent=self.app,
            )
            self.destroy()

        self.app.run_pdf_export(
            title="Priprema formalnog odobrenja",
            task=task,
            on_success=complete,
        )


class LineItemDialog(tk.Toplevel):
    def __init__(self, master: tk.Widget, initial: dict[str, Any] | None, on_save: Callable[[dict[str, Any]], None]) -> None:
        super().__init__(master)
        self.title("Stavka")
        self.configure(background=BG)
        self.on_save = on_save
        self.vars = {
            "category": tk.StringVar(value=str((initial or {}).get("category", CATEGORY_OPTIONS[0]))),
            "description": tk.StringVar(value=str((initial or {}).get("description", ""))),
            "unit": tk.StringVar(value=str((initial or {}).get("unit", UNIT_OPTIONS[0]))),
            "quantity": tk.StringVar(value=str((initial or {}).get("quantity", ""))),
            "unit_price": tk.StringVar(value=str((initial or {}).get("unit_price", ""))),
            "discount_percent": tk.StringVar(value=str((initial or {}).get("discount_percent", "0"))),
            "code_stage": tk.StringVar(value=str((initial or {}).get("code_stage", ""))),
        }
        frm = ttk.Frame(self, style="App.TFrame", padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        category_combo = add_combo(frm, 0, 0, "Kategorija", self.vars["category"], list(CATEGORY_OPTIONS))
        desc_entry = add_field(frm, 1, 0, "Opis", self.vars["description"], width=40)
        unit_combo = add_combo(frm, 2, 0, "Jedinica", self.vars["unit"], list(UNIT_OPTIONS))
        qty_entry = add_field(frm, 3, 0, "Količina", self.vars["quantity"], width=18)
        price_entry = add_field(frm, 4, 0, "Cena bez PDV", self.vars["unit_price"], width=18)
        discount_entry = add_field(frm, 5, 0, "Popust %", self.vars["discount_percent"], width=18)
        code_entry = add_field(frm, 6, 0, "Kod / etap", self.vars["code_stage"], width=18)
        self.widgets = [category_combo, desc_entry, unit_combo, qty_entry, price_entry, discount_entry, code_entry]
        self.description_entry = desc_entry
        btns = ttk.Frame(frm, style="App.TFrame")
        btns.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        paste_button = ttk.Button(btns, text="Nalepi iz Excela", command=self.paste_from_clipboard)
        paste_button.pack(side="left")
        add_tooltip(paste_button, "Učitava prvu prepoznatu stavku iz Excela u ovu formu. Za više redova koristi Nalepi iz Excela na kartici Stavke.")
        ttk.Button(btns, text="Sačuvaj", style="Primary.TButton", command=self.save).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Otkaži", command=self.destroy).pack(side="right")
        center_window(self, 520, 360)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        desc_entry.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: (self.save(), "break")[1])
        for widget in self.widgets:
            widget.bind("<Control-v>", self._smart_paste_handler)
            widget.bind("<Shift-Insert>", self._smart_paste_handler)
            widget.bind("<Control-Shift-V>", self._paste_handler)

    def save(self) -> None:
        try:
            payload = {k: v.get().strip() for k, v in self.vars.items()}
            payload["quantity"] = float(payload["quantity"])
            payload["unit_price"] = float(payload["unit_price"])
            payload["discount_percent"] = float(payload["discount_percent"] or 0)
        except ValueError:
            messagebox.showerror("Greška", "Proverite količinu, cenu i popust.")
            return
        self.on_save(payload)
        self.destroy()

    def _apply_payload(self, payload: dict[str, Any]) -> None:
        self.vars["category"].set(str(payload.get("category", CATEGORY_OPTIONS[0])) or CATEGORY_OPTIONS[0])
        self.vars["description"].set(str(payload.get("description", "")))
        self.vars["unit"].set(str(payload.get("unit", UNIT_OPTIONS[0])) or UNIT_OPTIONS[0])
        self.vars["quantity"].set(str(payload.get("quantity", "")))
        self.vars["unit_price"].set(str(payload.get("unit_price", "")))
        self.vars["discount_percent"].set(str(payload.get("discount_percent", "0")) or "0")
        self.vars["code_stage"].set(str(payload.get("code_stage", "")))
        if self.description_entry is not None:
            self.description_entry.focus_set()

    def _paste_from_clipboard_payloads(self) -> list[dict[str, Any]]:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return []
        payloads, _skipped, _has_header = clipboard_payloads_from_text(text)
        return payloads

    def paste_from_clipboard(self) -> None:
        payloads = self._paste_from_clipboard_payloads()
        if not payloads:
            messagebox.showinfo("Nalepi iz Excela", "Clipboard ne sadrži red koji mogu da prepoznam.")
            return
        self._apply_payload(payloads[0])
        if len(payloads) > 1:
            messagebox.showinfo("Nalepi iz Excela", "U clipboard-u ima više redova. U formu je učitana samo prva stavka.")

    def _paste_handler(self, event: tk.Event) -> str:
        self.paste_from_clipboard()
        return "break"

    def _smart_paste_handler(self, event: tk.Event) -> str | None:
        payloads = self._paste_from_clipboard_payloads()
        if not payloads:
            return None
        self._apply_payload(payloads[0])
        if len(payloads) > 1:
            messagebox.showinfo("Nalepi iz Excela", "U clipboard-u ima više redova. U formu je učitana samo prva stavka.")
        return "break"


class ClipboardPreviewDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Widget,
        payloads: list[dict[str, Any]],
        *,
        currency: str,
        vat_rate: float,
        skipped_rows: int,
        insert_hint: str,
        header_map: dict[str, int],
    ) -> None:
        super().__init__(master)
        self.title("Pregled clipboard uvoza")
        self.configure(background=BG)
        self.resizable(True, True)
        self.confirmed = False
        self.payloads = payloads

        frm = ttk.Frame(self, style="App.TFrame", padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(4, weight=1)

        ttk.Label(frm, text="Pregled clipboard stavki", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        summary = ttk.Frame(frm, style="Total.TFrame", padding=(12, 8))
        summary.grid(row=1, column=0, sticky="ew", pady=(8, 10))
        totals = calculate_invoice_totals(payloads, vat_rate=vat_rate, discount_total=0, retention_percent=0, advance_amount=0, paid_total=0, currency=currency)
        metrics = [
            ("STAVKE", str(len(payloads))),
            ("NETO", fmt_money(totals["subtotal"], currency)),
            ("PDV", fmt_money(totals["vat_total"], currency)),
            ("BRUTO", fmt_money(totals["gross_total"], currency)),
            ("PRESKOČENO", str(skipped_rows)),
            ("UBACIVANJE", insert_hint),
        ]
        for idx, (label, value) in enumerate(metrics):
            summary.columnconfigure(idx, weight=1 if label == "UBACIVANJE" else 0)
            ttk.Label(summary, text=label, style="TotalKey.TLabel").grid(row=0, column=idx, sticky="w", padx=(0, 18))
            ttk.Label(summary, text=value, style="TotalValue.TLabel").grid(row=1, column=idx, sticky="w", padx=(0, 18))

        ttk.Label(
            frm,
            text=f"Mapa Excel zaglavlja: {clipboard_mapping_summary(header_map)}",
            style="Help.TLabel",
            wraplength=1060,
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))
        categories_in_order = list(dict.fromkeys(str(payload.get("category") or "Ostalo") for payload in payloads))
        ttk.Label(
            frm,
            text=f"Redosled u fakturi: {'  ->  '.join(categories_in_order)}. Stavke se uvoze istim redom kao u Excelu.",
            style="Help.TLabel",
            wraplength=1060,
        ).grid(row=3, column=0, sticky="w", pady=(0, 8))

        table_frame = ttk.Frame(frm, style="App.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        cols = ("no", "category", "description", "unit", "quantity", "unit_price", "discount_percent", "net_amount", "vat_amount", "gross_amount", "code_stage")
        tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse", height=14)
        setup_treeview_tree(tree)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        for key, title, width, anchor in [
            ("no", "№", 48, "center"),
            ("category", "Kategorija", 110, "w"),
            ("description", "Opis", 300, "w"),
            ("unit", "JM", 60, "center"),
            ("quantity", "Količina", 85, "e"),
            ("unit_price", "Cena", 95, "e"),
            ("discount_percent", "Popust %", 80, "e"),
            ("net_amount", "Neto", 100, "e"),
            ("vat_amount", "PDV", 100, "e"),
            ("gross_amount", "Bruto", 100, "e"),
            ("code_stage", "Kod", 90, "w"),
        ]:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor=anchor)

        category_palette = ("#E6F5F1", "#ECF3FF", "#FFF4E6", "#F4EEFF", "#FCEEF2")
        category_tags: dict[str, str] = {}
        for index, category in enumerate(categories_in_order):
            tag = f"preview_category_{index}"
            category_tags[category] = tag
            tree.tag_configure(tag, background=category_palette[index % len(category_palette)])

        for idx, payload in enumerate(payloads, start=1):
            line = {
                "quantity": payload.get("quantity"),
                "unit_price": payload.get("unit_price"),
                "discount_percent": payload.get("discount_percent", 0),
            }
            calc = calculate_invoice_totals([line], vat_rate=vat_rate, discount_total=0, retention_percent=0, advance_amount=0, paid_total=0, currency=currency)
            tree.insert(
                "",
                "end",
                values=(
                    idx,
                    payload.get("category", ""),
                    payload.get("description", ""),
                    payload.get("unit", ""),
                    format_clipboard_number(payload.get("quantity", "")),
                    format_clipboard_number(payload.get("unit_price", "")),
                    format_clipboard_percent(payload.get("discount_percent", "")),
                    fmt_money(calc["subtotal"], currency),
                    fmt_money(calc["vat_total"], currency),
                    fmt_money(calc["gross_total"], currency),
                    payload.get("code_stage", ""),
                ),
                tags=(category_tags.get(str(payload.get("category") or "Ostalo"), ""),),
            )

        btns = ttk.Frame(frm, style="App.TFrame")
        btns.grid(row=5, column=0, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Uvezi", style="Primary.TButton", command=self.confirm).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Otkaži", command=self.cancel).pack(side="left")

        maximize_large_window(self, minimum_width=820, minimum_height=520)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Control-Return>", lambda e: (self.confirm(), "break")[1])
        self.bind("<Return>", lambda e: (self.confirm(), "break")[1])
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.wait_window(self)

    def confirm(self) -> None:
        self.confirmed = True
        self.destroy()

    def cancel(self) -> None:
        self.destroy()


class SendEmailDialog(tk.Toplevel):
    def __init__(self, master: tk.Widget, app: MainApp, invoice_id: int, on_sent: Callable[[], None] | None = None) -> None:
        super().__init__(master)
        self.app = app
        self.db = app.db
        self.invoice_id = invoice_id
        self.on_sent = on_sent
        self.title("Pošalji fakturu e-mailom")
        self.configure(background=BG)
        self.resizable(True, True)

        self.invoice = self.db.invoice_export_payload(invoice_id)
        self.company = app.company
        defaults = build_invoice_email_defaults(self.invoice, self.company)
        self.vars = {
            "recipient": tk.StringVar(value=defaults["recipient"]),
            "subject": tk.StringVar(value=defaults["subject"]),
            "include_pdf": tk.BooleanVar(value=True),
            "include_xlsx": tk.BooleanVar(value=False),
            "include_attachments": tk.BooleanVar(value=True),
        }

        frm = ttk.Frame(self, style="App.TFrame", padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        recipient_entry = add_field(frm, 0, 0, "Primaoc", self.vars["recipient"], width=42)
        add_field(frm, 1, 0, "Naslov", self.vars["subject"], width=52)

        ttk.Label(frm, text="Poruka").grid(row=2, column=0, sticky="nw", padx=(0, 6), pady=3)
        self.body_text = tk.Text(
            frm,
            height=12,
            wrap="word",
            background="white",
            foreground=TEXT,
            insertbackground=TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
        )
        self.body_text.grid(row=2, column=1, sticky="nsew", padx=(0, 14), pady=3)
        self.body_text.insert("1.0", defaults["body"])

        options = ttk.Frame(frm, style="App.TFrame")
        options.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(options, text="Priloži PDF fakturu", variable=self.vars["include_pdf"]).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(options, text="Priloži Excel kopiju", variable=self.vars["include_xlsx"]).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(options, text="Priloži sve postojeće dokumente", variable=self.vars["include_attachments"]).pack(side="left", padx=(0, 12))

        smtp_info = ttk.Label(
            frm,
            text=(
                f"SMTP: {self.company.get('smtp_host', '')}:{self.company.get('smtp_port', '')} "
                f"| Pošiljalac: {self.company.get('smtp_from_email') or self.company.get('email') or ''}"
            ),
            foreground=MUTED,
        )
        smtp_info.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frm, style="App.TFrame")
        btns.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(btns, text="Pošalji", style="Primary.TButton", command=self.send).pack(side="left")
        ttk.Button(btns, text="Otkaži", command=self.destroy).pack(side="right")
        center_window(self, 760, 520)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        recipient_entry.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: (self.send(), "break")[1])

    def send(self) -> None:
        recipient = self.vars["recipient"].get().strip()
        subject = self.vars["subject"].get().strip()
        body = self.body_text.get("1.0", "end").strip()
        if not recipient:
            messagebox.showerror("E-mail", "Unesite primaoca.")
            return
        try:
            send_invoice_email(
                self.invoice,
                self.company,
                recipient,
                subject,
                body,
                include_pdf=self.vars["include_pdf"].get(),
                include_xlsx=self.vars["include_xlsx"].get(),
                include_invoice_attachments=self.vars["include_attachments"].get(),
            )
        except Exception as exc:
            messagebox.showerror("E-mail", f"Slanje nije uspelo:\n{exc}")
            return
        messagebox.showinfo("E-mail", "Faktura je poslata.")
        if self.on_sent:
            self.on_sent()
        self.destroy()


class SMTPTestDialog(tk.Toplevel):
    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.company = app.company
        self.title("Test SMTP")
        self.configure(background=BG)
        self.resizable(True, True)

        sender_name = str(self.company.get("smtp_from_name") or self.company.get("name") or APP_NAME).strip()
        sender_email = str(self.company.get("smtp_from_email") or self.company.get("email") or self.company.get("smtp_username") or "").strip()
        default_recipient = sender_email or str(self.company.get("email") or "").strip()
        self.vars = {
            "recipient": tk.StringVar(value=default_recipient),
            "subject": tk.StringVar(value=f"{APP_NAME} - test SMTP"),
        }

        body_default = "\n".join(
            [
                "Poštovani,",
                "",
                f"ovo je test poruka iz aplikacije {APP_NAME}.",
                "Ako ste ovo primili, SMTP podešavanja rade ispravno.",
                "",
                "Srdačan pozdrav,",
                sender_name,
            ]
        )

        frm = ttk.Frame(self, style="App.TFrame", padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        recipient_entry = add_field(frm, 0, 0, "Primaoc", self.vars["recipient"], width=42)
        add_field(frm, 1, 0, "Naslov", self.vars["subject"], width=52)
        ttk.Label(frm, text="Poruka").grid(row=2, column=0, sticky="nw", padx=(0, 6), pady=3)
        self.body_text = tk.Text(
            frm,
            height=10,
            wrap="word",
            background="white",
            foreground=TEXT,
            insertbackground=TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
        )
        self.body_text.grid(row=2, column=1, sticky="nsew", padx=(0, 14), pady=3)
        self.body_text.insert("1.0", body_default)
        ttk.Label(
            frm,
            text=(
                f"SMTP: {self.company.get('smtp_host', '')}:{self.company.get('smtp_port', '')} "
                f"| Pošiljalac: {sender_email or self.company.get('smtp_username') or self.company.get('email') or ''}"
            ),
            foreground=MUTED,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        btns = ttk.Frame(frm, style="App.TFrame")
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(btns, text="Pošalji test", style="Primary.TButton", command=self.send).pack(side="left")
        ttk.Button(btns, text="Otkaži", command=self.destroy).pack(side="right")
        center_window(self, 720, 460)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        recipient_entry.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: (self.send(), "break")[1])

    def send(self) -> None:
        recipient = self.vars["recipient"].get().strip()
        subject = self.vars["subject"].get().strip()
        body = self.body_text.get("1.0", "end").strip()
        if not recipient:
            messagebox.showerror("Test SMTP", "Unesite primaoca.")
            return

        sender_name = str(self.company.get("smtp_from_name") or self.company.get("name") or APP_NAME).strip()
        sender_email = str(self.company.get("smtp_from_email") or self.company.get("email") or self.company.get("smtp_username") or "").strip()
        if not sender_email:
            messagebox.showerror("Test SMTP", "Unesite pošiljaoca u podešavanjima firme.")
            return

        message = EmailMessage()
        message["To"] = recipient
        message["From"] = formataddr((sender_name, sender_email))
        message["Subject"] = subject or f"{APP_NAME} - test SMTP"
        reply_to = str(self.company.get("smtp_reply_to") or sender_email).strip()
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(body or f"Ovo je test poruka iz {APP_NAME}.")

        try:
            send_message_via_smtp(self.company, message)
        except Exception as exc:
            messagebox.showerror("Test SMTP", f"Slanje nije uspelo:\n{exc}")
            return

        messagebox.showinfo("Test SMTP", "Test poruka je poslata.")
        self.destroy()


class PdfExportProgressDialog(tk.Toplevel):
    """Non-modal progress window for a native Excel PDF conversion."""

    def __init__(
        self,
        master: tk.Widget,
        *,
        title: str,
        task: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        on_finished: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.task = task
        self.on_success = on_success
        self.on_error = on_error
        self.on_finished = on_finished
        self.results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        self.title(title)
        self.configure(background=BG)
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.iconify)

        outer = ttk.Frame(self, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="PDF se priprema iz originalnog Excel šablona", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Excel radi u pozadini. Možete nastaviti rad u OpsNest-u; pregled će se otvoriti čim bude spreman.",
            style="Help.TLabel",
            wraplength=430,
        ).pack(anchor="w", pady=(6, 12))
        self.progress = ttk.Progressbar(outer, mode="indeterminate", length=430)
        self.progress.pack(fill="x")
        self.progress.start(12)
        center_window(self, 500, 165)

        threading.Thread(target=self._work, name="opsnest-pdf-export", daemon=True).start()
        self.after(100, self._poll)

    def _work(self) -> None:
        try:
            self.results.put((True, self.task()))
        except Exception as exc:
            self.results.put((False, exc))

    def _poll(self) -> None:
        try:
            success, result = self.results.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self.after(120, self._poll)
            return
        self.progress.stop()
        self.destroy()
        self.on_finished()
        if success:
            self.on_success(result)
        else:
            self.on_error(result)


class InvoiceTemplateDialog(tk.Toplevel):
    """Manage safe, copied Excel forms without ever editing the original template."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.db = app.db
        self.title("Šabloni fakture")
        self.configure(background=BG)
        self.minsize(820, 460)

        shell = ttk.Frame(self, style="App.TFrame", padding=16)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Šabloni fakture", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            shell,
            text=(
                "Originalni OpsNest/Delta Excel šablon je zaštićen. Svaki obrazac koji uvezete "
                "kopira se u lokalnu bazu i ostaje vezan uz fakture koje ga koriste."
            ),
            foreground=MUTED,
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))

        columns = ("name", "type", "default", "status")
        self.tree = ttk.Treeview(shell, columns=columns, show="headings", height=12, selectmode="browse")
        for key, label, width in (
            ("name", "Naziv obrasca", 390),
            ("type", "Izvor", 175),
            ("default", "Podrazumevani", 150),
            ("status", "Status", 130),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=110, anchor="w")
        tree_scroll = ttk.Scrollbar(shell, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())

        actions = ttk.Frame(self, style="App.TFrame", padding=(16, 0, 16, 16))
        actions.pack(fill="x")
        ttk.Button(actions, text="Uvezi Excel obrazac", style="Primary.TButton", command=self.import_template).pack(side="left")
        ttk.Button(actions, text="Postavi kao podrazumevani", command=self.set_default).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Otvori obrazac", command=self.open_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Arhiviraj", command=self.archive_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Osveži", command=self.refresh).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Zatvori", command=self.destroy).pack(side="right")

        center_window(self, 980, 585)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.refresh()

    def _selected_id(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def refresh(self) -> None:
        selected_id = self._selected_id()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for template in self.db.list_invoice_templates(include_archived=True):
            template_id = int(template["id"])
            is_original = template_id == 0
            values = (
                template.get("name") or "-",
                "Zaštićen original" if is_original else "Korisnički Excel",
                "Da" if int(template.get("is_default") or 0) else "",
                "Arhiviran" if int(template.get("archived") or 0) else "Aktivan",
            )
            self.tree.insert("", "end", iid=str(template_id), values=values)
        if selected_id is not None and self.tree.exists(str(selected_id)):
            self.tree.selection_set(str(selected_id))
            self.tree.focus(str(selected_id))

    def import_template(self) -> None:
        if not self.app.plan_includes_feature("custom_invoice_templates"):
            messagebox.showinfo(
                "Korisnički obrazac",
                "Uvoz sopstvenih obrazaca dostupan je u Business i Pro paketu.",
                parent=self,
            )
            return
        source = filedialog.askopenfilename(
            parent=self,
            title="Izaberite Excel obrazac fakture",
            filetypes=[("Excel radna sveska", "*.xlsx"), ("Sve datoteke", "*.*")],
        )
        if not source:
            return
        suggested_name = Path(source).stem.replace("_", " ")
        name = simpledialog.askstring("Naziv obrasca", "Naziv koji će korisnici videti:", initialvalue=suggested_name, parent=self)
        if name is None:
            return
        try:
            template_id = self.db.save_invoice_template(Path(source), name=name)
        except Exception as exc:
            messagebox.showerror("Korisnički obrazac", str(exc), parent=self)
            return
        self.refresh()
        self.tree.selection_set(str(template_id))
        self.tree.focus(str(template_id))
        messagebox.showinfo("Korisnički obrazac", "Obrazac je bezbedno kopiran u OpsNest.", parent=self)

    def set_default(self) -> None:
        template_id = self._selected_id()
        if template_id is None:
            messagebox.showinfo("Šabloni fakture", "Izaberite obrazac iz liste.", parent=self)
            return
        try:
            self.db.set_default_invoice_template(template_id)
        except Exception as exc:
            messagebox.showerror("Šabloni fakture", str(exc), parent=self)
            return
        self.refresh()

    def open_selected(self) -> None:
        template_id = self._selected_id()
        if template_id is None:
            messagebox.showinfo("Šabloni fakture", "Izaberite obrazac iz liste.", parent=self)
            return
        try:
            open_path(self.db.invoice_template_path(template_id))
        except Exception as exc:
            messagebox.showerror("Šabloni fakture", str(exc), parent=self)

    def archive_selected(self) -> None:
        template_id = self._selected_id()
        if template_id is None:
            messagebox.showinfo("Šabloni fakture", "Izaberite obrazac iz liste.", parent=self)
            return
        if template_id == 0:
            messagebox.showinfo("Šabloni fakture", "Originalni OpsNest/Delta obrazac je zaštićen i ne može se arhivirati.", parent=self)
            return
        if not messagebox.askyesno(
            "Arhiviraj obrazac",
            "Obrazac više neće biti ponuđen za nove fakture. Postojeće fakture će i dalje koristiti svoju sačuvanu kopiju. Nastaviti?",
            parent=self,
        ):
            return
        try:
            self.db.archive_invoice_template(template_id)
        except Exception as exc:
            messagebox.showerror("Šabloni fakture", str(exc), parent=self)
            return
        self.refresh()


class InvoiceApprovalDialog(tk.Toplevel):
    """Owner review queue for invoices submitted by accounting-team members."""

    def __init__(self, master: tk.Widget, app: MainApp) -> None:
        super().__init__(master)
        self.app = app
        self.db = app.db
        self.title("Odobravanje faktura")
        self.configure(background=BG)
        self.minsize(960, 520)

        shell = ttk.Frame(self, style="App.TFrame", padding=16)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Fakture na čekanju", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            shell,
            text=(
                "Knjigovođa može pripremiti fakturu, ali ona ne postaje izdata dok je vlasnik ili administrator "
                "ne pregleda i odobri. Otvorite fakturu da proverite PDF i Excel obrazac pre odobravanja."
            ),
            foreground=MUTED,
            wraplength=1100,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))

        columns = ("number", "project", "customer", "prepared", "date", "total")
        self.tree = ttk.Treeview(shell, columns=columns, show="headings", height=13, selectmode="browse")
        for key, label, width in (
            ("number", "Broj", 135),
            ("project", "Projekat", 190),
            ("customer", "Kupac", 220),
            ("prepared", "Pripremio", 175),
            ("date", "Poslato", 165),
            ("total", "Ukupno", 150),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=90, anchor="w")
        tree_scroll = ttk.Scrollbar(shell, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())

        actions = ttk.Frame(self, style="App.TFrame", padding=(16, 0, 16, 16))
        actions.pack(fill="x")
        ttk.Button(actions, text="Otvori i pregledaj", style="Primary.TButton", command=self.open_selected).pack(side="left")
        ttk.Button(actions, text="Vrati na doradu", command=self.return_selected_for_revision).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Odobri za izdavanje", command=self.approve_selected).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="Odobri i izdaj",
            style="Primary.TButton",
            command=lambda: self.approve_selected(issue_after_approval=True),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Osveži", command=self.refresh).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Zatvori", command=self.destroy).pack(side="right")

        center_window(self, 1220, 650)
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.refresh()

    def _selected_id(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def refresh(self) -> None:
        selected_id = self._selected_id()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for invoice in self.db.pending_invoice_approvals():
            invoice_id = int(invoice["id"])
            submitted_at = str(invoice.get("updated_at") or "")
            try:
                submitted_at = format_date(submitted_at[:10]) + (" " + submitted_at[11:16] if len(submitted_at) >= 16 else "")
            except Exception:
                pass
            self.tree.insert(
                "",
                "end",
                iid=str(invoice_id),
                values=(
                    invoice.get("invoice_number") or "-",
                    invoice.get("project_name") or "-",
                    invoice.get("customer_name") or "-",
                    invoice.get("prepared_by_name") or invoice.get("prepared_by_role") or "-",
                    submitted_at or "-",
                    format_currency(invoice.get("gross_total"), invoice.get("currency") or DEFAULT_CURRENCY),
                ),
            )
        if selected_id is not None and self.tree.exists(str(selected_id)):
            self.tree.selection_set(str(selected_id))
            self.tree.focus(str(selected_id))

    def open_selected(self) -> None:
        invoice_id = self._selected_id()
        if invoice_id is None:
            messagebox.showinfo("Odobravanje faktura", "Izaberite fakturu iz liste.", parent=self)
            return
        self.db.mark_owner_notifications_read(invoice_id=invoice_id)
        editor = InvoiceEditor(self.app, self.db, invoice_id=invoice_id)
        self.wait_window(editor)
        self.app.refresh_all()
        self.refresh()

    def return_selected_for_revision(self) -> None:
        invoice_id = self._selected_id()
        if invoice_id is None:
            messagebox.showinfo("Odobravanje faktura", "Izaberite fakturu iz liste.", parent=self)
            return
        invoice = self.db.get_invoice(invoice_id)
        if not invoice:
            self.refresh()
            return
        comment = simpledialog.askstring(
            "Vrati na doradu",
            f"Komentar za knjigovođu za fakturu {invoice.get('invoice_number') or '-'} (obavezno):",
            parent=self,
        )
        if comment is None:
            return
        try:
            self.db.return_invoice_for_revision(
                invoice_id,
                self.app.active_team_member_name(),
                comment,
            )
        except Exception as exc:
            messagebox.showerror("Vrati na doradu", str(exc), parent=self)
            return
        self.app.refresh_all()
        self.refresh()
        messagebox.showinfo(
            "Vrati na doradu",
            "Faktura je vraćena u nacrt. Komentar je sačuvan u istoriji fakture.",
            parent=self,
        )

    def approve_selected(self, *, issue_after_approval: bool = False) -> None:
        invoice_id = self._selected_id()
        if invoice_id is None:
            messagebox.showinfo("Odobravanje faktura", "Izaberite fakturu iz liste.", parent=self)
            return
        invoice = self.db.get_invoice(invoice_id)
        if not invoice:
            self.refresh()
            return
        confirmation = (
            f"Odobriti i izdati fakturu {invoice.get('invoice_number') or '-'}? "
            "Nakon potvrde broj, PDF i Excel kopija se odmah zaključavaju."
            if issue_after_approval
            else f"Odobriti fakturu {invoice.get('invoice_number') or '-'}? "
            "Računovođa je zatim može izdati i poslati kupcu."
        )
        if not messagebox.askyesno(
            "Odobravanje fakture",
            confirmation,
            parent=self,
        ):
            return
        try:
            self.db.approve_invoice(
                invoice_id,
                self.app.active_team_member_name(),
                issue_after_approval=issue_after_approval,
            )
            if issue_after_approval:
                self.app.queue_invoice_output_export(invoice_id)
        except Exception as exc:
            messagebox.showerror("Odobravanje faktura", str(exc), parent=self)
            return
        self.app.refresh_all()
        self.refresh()
        messagebox.showinfo(
            "Odobravanje faktura",
            "Faktura je odobrena i izdata. PDF i Excel se generišu u pozadini."
            if issue_after_approval
            else "Faktura je odobrena za izdavanje.",
            parent=self,
        )


class InvoiceEditor(tk.Toplevel):
    def __init__(
        self,
        app: MainApp,
        db: Database,
        invoice_id: int | None = None,
        *,
        initial_project_id: int | None = None,
        correction_invoice_id: int | None = None,
        initial_tab: str = "details",
        initial_invoice_kind: str = "standard",
    ) -> None:
        super().__init__(app)
        self.app = app
        self.db = db
        self.invoice_id = invoice_id
        self.initial_project_id = initial_project_id
        self.correction_invoice_id = correction_invoice_id
        self.initial_tab = initial_tab
        self.initial_invoice_kind = initial_invoice_kind if initial_invoice_kind in INVOICE_KINDS else "standard"
        self.title("Faktura")
        self.configure(background=BG)
        screen_width, screen_height = desktop_work_area(self)
        minimum_width = min(1080, screen_width)
        minimum_height = min(680, screen_height)
        window_width = min(1420, max(minimum_width, screen_width - 80))
        window_height = min(900, max(minimum_height, screen_height - 100))
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(minimum_width, minimum_height)
        self._windowed_geometry = f"{window_width}x{window_height}"
        self._window_mode_button: ttk.Button | None = None
        self.transient(app)
        self.grab_set()

        self.customer_map: dict[str, int] = {}
        self.project_map: dict[str, int] = {}
        self.template_map: dict[str, int] = {}
        self.advance_source_map: dict[str, int] = {}
        self.item_data: list[dict[str, Any]] = []
        self.payment_rows: list[dict[str, Any]] = []
        self.attachment_rows: list[dict[str, Any]] = []
        self.customer_combo: ttk.Combobox | None = None
        self.project_combo: ttk.Combobox | None = None
        self.invoice_template_combo: ttk.Combobox | None = None
        self.advance_source_combo: ttk.Combobox | None = None
        self.currency_combo: ttk.Combobox | None = None
        self.issue_date_entry: ttk.Entry | None = None
        self.tax_event_date_entry: ttk.Entry | None = None
        self.due_date_entry: ttk.Entry | None = None
        self.customer_term_entry: ttk.Entry | None = None
        self.discount_total_entry: ttk.Entry | None = None
        self.retention_percent_entry: ttk.Entry | None = None
        self.advance_amount_entry: ttk.Entry | None = None
        self.lines_quick_frame: ttk.LabelFrame | None = None
        self.lines_toolbar: ttk.Frame | None = None
        self.advance_lines_notice: ttk.LabelFrame | None = None
        self.advance_lines_notice_var = tk.StringVar()
        self.quick_vars = {
            "category": tk.StringVar(value=CATEGORY_OPTIONS[0]),
            "description": tk.StringVar(),
            "unit": tk.StringVar(value=UNIT_OPTIONS[0]),
            "quantity": tk.StringVar(),
            "unit_price": tk.StringVar(),
            "discount_percent": tk.StringVar(value="0"),
            "code_stage": tk.StringVar(),
        }
        self.attachment_type_var = tk.StringVar(value=ATTACHMENT_TYPE_OPTIONS[0])

        self.vars = {
            "invoice_number": tk.StringVar(),
            "status_code": tk.StringVar(value="draft"),
            "invoice_kind": tk.StringVar(value=INVOICE_KIND_LABELS[self.initial_invoice_kind]),
            "advance_source_invoice_id": tk.StringVar(),
            "issue_date": tk.StringVar(value=date.today().strftime("%d.%m.%Y")),
            "tax_event_date": tk.StringVar(value=date.today().strftime("%d.%m.%Y")),
            "due_date": tk.StringVar(value=(date.today() + timedelta(days=DEFAULT_PAYMENT_TERM_DAYS)).strftime("%d.%m.%Y")),
            "currency": tk.StringVar(value=str(app.company.get("default_currency") or DEFAULT_CURRENCY)),
            "payment_method": tk.StringVar(value=payment_method_default()),
            "issue_place": tk.StringVar(value=app.company.get("issue_place", "Sofija")),
            "customer_id": tk.StringVar(),
            "project_id": tk.StringVar(),
            "invoice_template_id": tk.StringVar(),
            "document_language": tk.StringVar(),
            "project_name": tk.StringVar(),
            "site_address": tk.StringVar(),
            "contract_no": tk.StringVar(),
            "contract_net_amount": tk.StringVar(value="0"),
            "advance_percent": tk.StringVar(value="0"),
            "protocol_no": tk.StringVar(),
            "period_from": tk.StringVar(),
            "period_to": tk.StringVar(),
            "order_reference": tk.StringVar(),
            "customer_name": tk.StringVar(),
            "customer_eik": tk.StringVar(),
            "customer_vat": tk.StringVar(),
            "customer_address": tk.StringVar(),
            "customer_contact": tk.StringVar(),
            "customer_phone": tk.StringVar(),
            "customer_email": tk.StringVar(),
            "customer_payment_term_days": tk.StringVar(value=str(DEFAULT_PAYMENT_TERM_DAYS)),
            "discount_total": tk.StringVar(value="0"),
            "retention_percent": tk.StringVar(value="0"),
            "advance_amount": tk.StringVar(value="0"),
            "note": tk.StringVar(),
            "subtotal": tk.StringVar(value="0"),
            "tax_base": tk.StringVar(value="0"),
            "vat_total": tk.StringVar(value="0"),
            "vat_caption": tk.StringVar(value="PDV"),
            "gross_total": tk.StringVar(value="0"),
            "retention_amount": tk.StringVar(value="0"),
            "due_before_paid": tk.StringVar(value="0"),
            "paid_total": tk.StringVar(value="0"),
            "balance_total": tk.StringVar(value="0"),
        }

        self._build()
        if self.initial_tab == "attachments":
            self.nb.select(self.attachments_tab)
        elif self.initial_tab == "payments":
            self.nb.select(self.payments_tab)
        elif self.initial_tab == "items":
            self.nb.select(self.lines_tab)
        self._load_lists(selected_project_id=initial_project_id)
        self._load_invoice_templates()
        self._set_default_document_language()
        if invoice_id:
            self._load_invoice()
        elif correction_invoice_id:
            self._load_correction_draft(correction_invoice_id)
        else:
            if initial_project_id:
                self._set_project_selection(initial_project_id)
                self._on_project_selected()
            self._on_invoice_kind_changed()
            self._apply_customer_terms()
            self._refresh_totals()
        # Database stores a stable code; the selector must show a localized
        # workflow caption rather than the raw internal value "draft".
        self.vars["status_code"].set(localized_status_label(status_code_from_display(self.vars["status_code"].get()) or "draft"))
        localize_widget_tree(self, self.app.ui_language)
        self.after_idle(self._maximize_window)

    def _set_default_document_language(self) -> None:
        """Keep the document language explicit and separate from app language."""
        current_value = self.vars["document_language"].get().strip()
        current = invoice_document_language_code_from_label(current_value)
        if current_value and current in INVOICE_DOCUMENT_LANGUAGE_LABELS:
            return
        country = str(self.app.company.get("country_code") or "").upper()
        default_language = "bg" if country == "BG" else "sr"
        self.vars["document_language"].set(INVOICE_DOCUMENT_LANGUAGE_LABELS[default_language])

    def _build(self) -> None:
        top = ttk.Frame(self, style="App.TFrame")
        top.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(top, text="Faktura", style="Section.TLabel").pack(side="left")
        ttk.Label(top, textvariable=self.vars["invoice_number"], foreground=TEXT, font=("Segoe UI", 11, "bold")).pack(side="left", padx=12)
        ttk.Label(top, textvariable=self.vars["status_code"], foreground=MUTED).pack(side="left")
        top_controls = ttk.Frame(top, style="App.TFrame")
        top_controls.pack(side="right")
        ttk.Button(top_controls, text="Smanji", command=self.iconify).pack(side="left", padx=(0, 6))
        self._window_mode_button = ttk.Button(top_controls, text="Uvećaj", command=self.toggle_window_mode)
        self._window_mode_button.pack(side="left")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.details_tab = ttk.Frame(self.nb, style="App.TFrame")
        self.lines_tab = ttk.Frame(self.nb, style="App.TFrame")
        self.payments_tab = ttk.Frame(self.nb, style="App.TFrame")
        self.attachments_tab = ttk.Frame(self.nb, style="App.TFrame")
        self.nb.add(self.details_tab, text="Detalji")
        self.nb.add(self.lines_tab, text="Stavke")
        self.nb.add(self.payments_tab, text="Uplate")
        self.nb.add(self.attachments_tab, text="Prilozi")

        self._build_details_tab()
        self._build_lines_tab()
        self._build_payments_tab()
        self._build_attachments_tab()

        footer = ttk.Frame(self, style="App.TFrame")
        footer.pack(fill="x", padx=10, pady=(0, 10))
        footer.columnconfigure(0, weight=1)
        totals_strip = ttk.Frame(footer, style="Total.TFrame", padding=(12, 7))
        totals_strip.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        total_cells = [
            ("Neto", self.vars["subtotal"], "TotalValue.TLabel"),
            (self.vars["vat_caption"], self.vars["vat_total"], "TotalValue.TLabel"),
            ("Ukupno", self.vars["gross_total"], "TotalValue.TLabel"),
            ("Za plaćanje", self.vars["balance_total"], "TotalDue.TLabel"),
        ]
        for label, value, value_style in total_cells:
            cell = ttk.Frame(totals_strip, style="Total.TFrame")
            cell.pack(side="left", padx=(0, 26))
            if isinstance(label, tk.StringVar):
                ttk.Label(cell, textvariable=label, style="TotalKey.TLabel").pack(anchor="w")
            else:
                ttk.Label(cell, text=label, style="TotalKey.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=value, style=value_style).pack(anchor="w", pady=(1, 0))
        utility_actions = ttk.Frame(footer, style="App.TFrame")
        utility_actions.grid(row=1, column=0, sticky="e", pady=(0, 4))
        ttk.Button(utility_actions, text="Pregled Excel", command=lambda: self.preview_invoice("xlsx")).pack(side="left", padx=3)
        ttk.Button(utility_actions, text="Pregled PDF / štampa", command=lambda: self.preview_invoice("pdf")).pack(side="left", padx=3)
        ttk.Button(utility_actions, text="Excel šablon", command=lambda: self.export_xlsx(open_after=True)).pack(side="left", padx=3)
        ttk.Button(utility_actions, text="PDF / štampa", command=lambda: self.export_pdf(open_after=True)).pack(side="left", padx=3)
        ttk.Button(utility_actions, text="Poveži e-fakture", command=self.open_einvoice_connection).pack(side="left", padx=3)
        ttk.Button(utility_actions, text="E-faktura provera", command=self.check_sef_readiness).pack(side="left", padx=3)
        ttk.Button(utility_actions, text="UBL 2.1 nacrt", command=self.export_ubl_draft).pack(side="left", padx=3)
        ttk.Button(utility_actions, text="SEF demo veza (Srbija)", command=self.test_sef_demo_connection).pack(side="left", padx=3)
        workflow_actions = ttk.Frame(footer, style="App.TFrame")
        workflow_actions.grid(row=2, column=0, sticky="e")
        ttk.Button(workflow_actions, text="Dodaj uplatu", command=self.add_payment).pack(side="left", padx=3)
        ttk.Button(workflow_actions, text="E-mail", command=self.send_email).pack(side="left", padx=3)
        ttk.Button(workflow_actions, text="Sačuvaj nacrt", command=lambda: self.save_invoice("draft")).pack(side="left", padx=3)
        ttk.Button(workflow_actions, text="Pošalji na proveru", command=lambda: self.save_invoice("pending_approval")).pack(side="left", padx=3)
        ttk.Button(workflow_actions, text="Odobri", command=lambda: self.save_invoice("approved")).pack(side="left", padx=3)
        ttk.Button(workflow_actions, text="Izdaj fakturu", style="Primary.TButton", command=lambda: self.save_invoice("issued")).pack(side="left", padx=3)
        ttk.Button(workflow_actions, text="Obriši nacrt", command=self.delete_draft).pack(side="left", padx=3)
        ttk.Button(workflow_actions, text="Zatvori", command=self.destroy).pack(side="left", padx=3)
        audit_actions = ttk.Frame(footer, style="App.TFrame")
        audit_actions.grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Button(audit_actions, text="Istorija fakture", command=self.open_invoice_history).pack(side="left", padx=(0, 6))
        ttk.Button(audit_actions, text="E-faktura outbox", command=self.open_einvoice_outbox).pack(side="left", padx=3)
        ttk.Button(audit_actions, text="Ponavljaj fakturu", command=self.open_recurring_template_dialog).pack(side="left", padx=3)
        ttk.Button(audit_actions, text="Povraćaj / odobrenje", command=self.add_payment_refund).pack(side="left", padx=3)
        ttk.Button(audit_actions, text="Izdaj formalno odobrenje", command=self.create_credit_note).pack(side="left", padx=3)
        ttk.Button(audit_actions, text="Napravi ispravku", command=self.create_correction_draft).pack(side="left", padx=3)
        ttk.Button(audit_actions, text="Storniraj fakturu", command=self.cancel_invoice).pack(side="left", padx=3)

    def _maximize_window(self) -> None:
        self.update_idletasks()
        self._windowed_geometry = self.geometry()
        try:
            self.state("zoomed")
        except tk.TclError:
            screen_width, screen_height = desktop_work_area(self)
            width = min(screen_width - 40, 1420)
            height = min(screen_height - 80, 900)
            center_window(self, width, height)
        self._update_window_button()

    def _update_window_button(self) -> None:
        if self._window_mode_button is None:
            return
        self._window_mode_button.configure(text=tr("Vrati veličinu" if self.state() == "zoomed" else "Uvećaj"))

    def toggle_window_mode(self) -> None:
        if self.state() == "zoomed":
            try:
                self.state("normal")
            except tk.TclError:
                pass
            if self._windowed_geometry:
                try:
                    self.geometry(self._windowed_geometry)
                except tk.TclError:
                    screen_width, screen_height = desktop_work_area(self)
                    center_window(self, min(screen_width - 40, 1420), min(screen_height - 80, 900))
            else:
                screen_width, screen_height = desktop_work_area(self)
                center_window(self, min(screen_width - 40, 1420), min(screen_height - 80, 900))
        else:
            self._windowed_geometry = self.geometry()
            self._maximize_window()
            return
        self._update_window_button()

    def _build_details_tab(self) -> None:
        outer = ScrollableFrame(self.details_tab)
        outer.pack(fill="both", expand=True)
        frm = outer.inner
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(3, weight=1)

        template_hint = ttk.Frame(frm, style="Total.TFrame", padding=(10, 8))
        template_hint.grid(row=0, column=0, columnspan=4, sticky="ew", padx=(0, 14), pady=(0, 8))
        ttk.Label(template_hint, text="Forma fakture", style="TotalValue.TLabel").pack(side="left")
        self.invoice_template_combo = ttk.Combobox(
            template_hint,
            textvariable=self.vars["invoice_template_id"],
            width=38,
            state="readonly",
            style="Modern.TCombobox",
        )
        self.invoice_template_combo.pack(side="left", padx=(10, 8))
        ttk.Button(template_hint, text="Upravljaj obrascima", command=self._manage_invoice_templates).pack(side="left")
        ttk.Button(template_hint, text="Otvori obrazac", command=self._open_selected_invoice_template).pack(side="left", padx=(8, 0))
        ttk.Label(
            template_hint,
            text="Originalni Delta obrazac je zaštićen; sopstveni obrazac se čuva kao posebna kopija.",
            style="TotalKey.TLabel",
        ).pack(side="right", padx=(12, 0))
        row = 1
        add_field(frm, row, 0, "Broj fakture", self.vars["invoice_number"], readonly=True)
        add_combo(frm, row, 2, "Status", self.vars["status_code"], [STATUS_LABELS[c] for c in STATUS_CODES if c != "cancelled"])
        row += 1
        invoice_kind_values = [INVOICE_KIND_LABELS[kind] for kind in INVOICE_KINDS]
        invoice_kind_combo = add_combo(frm, row, 0, "Vrsta računa", self.vars["invoice_kind"], invoice_kind_values, width=24)
        self.advance_source_combo = add_combo(frm, row, 2, "Plaćeni avans", self.vars["advance_source_invoice_id"], [], width=30)
        invoice_kind_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_invoice_kind_changed())
        self.advance_source_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_advance_source_selected())
        row += 1
        self.issue_date_entry = add_field(frm, row, 0, "Datum izdavanja", self.vars["issue_date"], width=18)
        self.tax_event_date_entry = add_field(frm, row, 2, "Datum poreskog događaja", self.vars["tax_event_date"], width=18)
        row += 1
        self.due_date_entry = add_field(frm, row, 0, "Rok plaćanja", self.vars["due_date"], width=18)
        self.currency_combo = add_combo(frm, row, 2, "Valuta", self.vars["currency"], list(SUPPORTED_CURRENCIES), width=12)
        row += 1
        add_combo(
            frm,
            row,
            0,
            "Jezik dokumenta za izvoz",
            self.vars["document_language"],
            list(INVOICE_DOCUMENT_LANGUAGE_LABELS.values()),
            width=24,
        )
        ttk.Label(
            frm,
            text="Menja fiksne oznake na Excel/PDF fakturi; opisi stavki ostaju tačno kako su uneti.",
            style="Help.TLabel",
            wraplength=340,
        ).grid(row=row, column=2, columnspan=2, sticky="w", padx=(0, 14), pady=3)
        row += 1
        self.customer_combo = add_combo(frm, row, 0, "Kupac", self.vars["customer_id"], [], width=30)
        self.project_combo = add_combo(frm, row, 2, "Projekat", self.vars["project_id"], [], width=30)
        row += 1
        actions = ttk.Frame(frm, style="App.TFrame")
        actions.grid(row=row, column=0, columnspan=4, sticky="w", padx=(0, 14), pady=(0, 3))
        ttk.Button(actions, text="Novi kupac", command=self.create_customer_from_invoice).pack(side="left")
        ttk.Button(actions, text="Novi projekat", command=self.create_project_from_invoice).pack(side="left", padx=6)
        ttk.Button(actions, text="Osveži liste", command=lambda: self._load_lists(selected_customer_id=self._selected_customer_id(), selected_project_id=self._selected_project_id())).pack(side="left", padx=6)
        row += 1
        add_combo(frm, row, 0, "Način plaćanja", self.vars["payment_method"], list(PAYMENT_METHOD_OPTIONS), width=24)
        add_field(frm, row, 2, "Mesto izdavanja", self.vars["issue_place"], width=18)
        row += 1
        add_field(frm, row, 0, "Projekat / objekat", self.vars["project_name"], width=34)
        add_field(frm, row, 2, "Adresa gradilišta", self.vars["site_address"], width=34)
        row += 1
        add_field(frm, row, 0, "Broj ugovora", self.vars["contract_no"], width=24)
        add_field(frm, row, 2, "Broj protokola / Akta 19", self.vars["protocol_no"], width=24)
        row += 1
        add_field(frm, row, 0, "Period od", self.vars["period_from"], width=18)
        add_field(frm, row, 2, "Period do", self.vars["period_to"], width=18)
        row += 1
        add_field(frm, row, 0, "Referenca", self.vars["order_reference"], width=24)
        self.customer_term_entry = add_field(frm, row, 2, "Kupac - rok (dani)", self.vars["customer_payment_term_days"], width=12)
        row += 1
        self.discount_total_entry = add_field(frm, row, 0, "Popust / korekcija", self.vars["discount_total"], width=18)
        self.retention_percent_entry = add_field(frm, row, 2, "Garancijsko zadržavanje %", self.vars["retention_percent"], width=18)
        row += 1
        self.advance_amount_entry = add_field(frm, row, 0, "Odbijeni avans", self.vars["advance_amount"], width=18)
        ttk.Label(frm, text="Napomena").grid(row=row, column=2, sticky="nw", padx=(0, 6), pady=3)
        self.note_text = tk.Text(
            frm,
            height=5,
            wrap="word",
            background="white",
            foreground=TEXT,
            insertbackground=TEXT,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=LINE,
        )
        self.note_text.grid(row=row, column=3, sticky="nsew", padx=(0, 14), pady=3)

        snapshot = ttk.LabelFrame(frm, text="Snapshot kupca", padding=10)
        snapshot.grid(row=1, column=4, rowspan=12, sticky="nsew", padx=(10, 0), pady=3)
        snapshot.columnconfigure(1, weight=1)
        self.snapshot_labels: dict[str, ttk.Label] = {}
        snap_fields = [
            ("customer_name", "Naziv"),
            ("customer_eik", "EIK"),
            ("customer_vat", "PDV"),
            ("customer_address", "Adresa"),
            ("customer_contact", "Lice"),
            ("customer_phone", "Telefon"),
            ("customer_email", "E-mail"),
        ]
        for idx, (key, label) in enumerate(snap_fields):
            ttk.Label(snapshot, text=label).grid(row=idx, column=0, sticky="w", pady=2)
            val = ttk.Label(snapshot, textvariable=self.vars[key], foreground=TEXT)
            val.grid(row=idx, column=1, sticky="w", pady=2)
            self.snapshot_labels[key] = val

        if self.customer_combo is not None:
            self.customer_combo.bind("<<ComboboxSelected>>", lambda e: self._on_customer_selected())
            self.customer_combo.bind("<Return>", lambda e: self._on_customer_selected())
            self.customer_combo.bind("<Control-n>", lambda e: (self.create_customer_from_invoice(), "break")[1])
        if self.project_combo is not None:
            self.project_combo.bind("<<ComboboxSelected>>", lambda e: self._on_project_selected())
            self.project_combo.bind("<Return>", lambda e: self._on_project_selected())
            self.project_combo.bind("<Control-n>", lambda e: (self.create_project_from_invoice(), "break")[1])
        if self.issue_date_entry is not None:
            self.issue_date_entry.bind("<FocusOut>", lambda e: self._sync_due_date())
        if self.customer_term_entry is not None:
            self.customer_term_entry.bind("<FocusOut>", lambda e: self._sync_due_date())
        if self.currency_combo is not None:
            self.currency_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_totals())
        if self.invoice_template_combo is not None:
            self.invoice_template_combo.bind("<<ComboboxSelected>>", lambda _event: None)
        for entry in [self.discount_total_entry, self.retention_percent_entry, self.advance_amount_entry]:
            if entry is not None:
                entry.bind("<FocusOut>", lambda e: self._refresh_totals())

    def _build_lines_tab(self) -> None:
        outer = ttk.Frame(self.lines_tab, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        invoice_kind_bar = ttk.Frame(outer, style="Total.TFrame", padding=(10, 7))
        invoice_kind_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(invoice_kind_bar, text="Vrsta računa", style="TotalKey.TLabel").pack(side="left")
        lines_invoice_kind_combo = ttk.Combobox(
            invoice_kind_bar,
            textvariable=self.vars["invoice_kind"],
            values=[INVOICE_KIND_LABELS[kind] for kind in INVOICE_KINDS],
            width=22,
            state="readonly",
            style="Modern.TCombobox",
        )
        lines_invoice_kind_combo.pack(side="left", padx=(10, 14))
        lines_invoice_kind_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_invoice_kind_changed())
        ttk.Label(
            invoice_kind_bar,
            text="Avans se računa iz ugovora projekta; za završni račun izaberite plaćeni avans u kartici Detalji.",
            style="TotalKey.TLabel",
        ).pack(side="left")
        self.advance_lines_notice = ttk.LabelFrame(outer, text="Ugovorni avans", padding=10)
        ttk.Label(self.advance_lines_notice, textvariable=self.advance_lines_notice_var, style="Help.TLabel", wraplength=1100).pack(anchor="w")
        quick = ttk.LabelFrame(outer, text="Brz unos stavke", padding=8)
        quick.pack(fill="x", pady=(0, 8))
        self.lines_quick_frame = quick
        for col in range(14):
            quick.columnconfigure(col, weight=1 if col in {1, 3, 5, 7, 9, 11, 13} else 0)
        self.quick_category_combo = add_combo(quick, 0, 0, "Kategorija", self.quick_vars["category"], list(CATEGORY_OPTIONS), width=14)
        self.quick_description_entry = add_field(quick, 0, 2, "Opis", self.quick_vars["description"], width=36)
        self.quick_unit_combo = add_combo(quick, 0, 4, "JM", self.quick_vars["unit"], list(UNIT_OPTIONS), width=10)
        self.quick_quantity_entry = add_field(quick, 0, 6, "Količina", self.quick_vars["quantity"], width=12)
        self.quick_price_entry = add_field(quick, 0, 8, "Cena bez PDV", self.quick_vars["unit_price"], width=14)
        self.quick_discount_entry = add_field(quick, 0, 10, "Popust %", self.quick_vars["discount_percent"], width=10)
        self.quick_code_entry = add_field(quick, 0, 12, "Kod / etap", self.quick_vars["code_stage"], width=12)
        self.quick_add_button = ttk.Button(quick, text="Dodaj", style="Primary.TButton", command=self.add_quick_line)
        self.quick_add_button.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(quick, text="Očisti", command=self.clear_quick_line).grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(8, 0))
        ttk.Button(quick, text="Učitaj izabranu", command=self.load_selected_line_into_quick_form).grid(row=1, column=2, sticky="w", padx=(6, 0), pady=(8, 0))
        ttk.Button(quick, text="Dupliraj izabranu", command=self.duplicate_selected_line).grid(row=1, column=3, sticky="w", padx=(6, 0), pady=(8, 0))
        paste_form_button = ttk.Button(quick, text="Nalepi u formu", command=self.paste_clipboard_into_quick_form)
        paste_form_button.grid(row=1, column=4, sticky="w", padx=(6, 0), pady=(8, 0))
        add_tooltip(paste_form_button, "Učitava prvu stavku iz clipboard-a u brza polja. Prečica u poljima: Ctrl+V.")
        paste_excel_button = ttk.Button(quick, text="Nalepi iz Excela", command=self.paste_lines_from_clipboard)
        paste_excel_button.grid(row=1, column=5, sticky="w", padx=(6, 0), pady=(8, 0))
        add_tooltip(paste_excel_button, "Prikazuje pregled svih prepoznatih Excel redova pre uvoza. Prečica: Ctrl+Shift+V.")
        ttk.Button(quick, text="Dodaj i novo", command=lambda: (self.add_quick_line(), self.quick_description_entry.focus_set() if self.quick_description_entry is not None else None)).grid(row=1, column=6, sticky="w", padx=(6, 0), pady=(8, 0))

        toolbar = ttk.Frame(outer, style="App.TFrame")
        toolbar.pack(fill="x", pady=(0, 6))
        self.lines_toolbar = toolbar
        ttk.Button(toolbar, text="Izmeni", command=self.edit_line).pack(side="left")
        ttk.Button(toolbar, text="Obriši", command=self.delete_line).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Kopiraj", command=self.copy_selected_lines_to_clipboard).pack(side="left", padx=4)
        paste_lines_button = ttk.Button(toolbar, text="Nalepi", command=self.paste_lines_from_clipboard)
        paste_lines_button.pack(side="left", padx=4)
        add_tooltip(paste_lines_button, "Uvezi više Excel stavki sa pregledom, bojama po kategoriji i ukupnim iznosima.")
        ttk.Button(toolbar, text="Dodaj iz projekta", command=self.import_project_lines).pack(side="left", padx=4)
        cols = ("no", "category", "desc", "unit", "qty", "price", "discount", "net", "vat", "gross", "code")
        self.lines_tree = ttk.Treeview(outer, columns=cols, show="headings", selectmode="extended")
        setup_treeview_tree(self.lines_tree)
        for key, title, width, anchor in [
            ("no", "№", 50, "center"),
            ("category", "Kategorija", 110, "w"),
            ("desc", "Opis", 350, "w"),
            ("unit", "JM", 70, "center"),
            ("qty", "Količina", 90, "e"),
            ("price", "Cena", 110, "e"),
            ("discount", "Popust %", 80, "e"),
            ("net", "Bez PDV", 110, "e"),
            ("vat", "PDV", 110, "e"),
            ("gross", "Ukupno", 110, "e"),
            ("code", "Kod", 90, "w"),
        ]:
            self.lines_tree.heading(key, text=title)
            self.lines_tree.column(key, width=width, anchor=anchor)
        self.lines_tree.pack(fill="both", expand=True)
        self.lines_tree.bind("<Double-1>", lambda e: (self.edit_line(), "break")[1])
        self.lines_tree.bind("<Delete>", lambda e: (self.delete_line(), "break")[1])
        self.lines_tree.bind("<Return>", lambda e: (self.edit_line(), "break")[1])
        self.lines_tree.bind("<Control-d>", lambda e: (self.duplicate_selected_line(), "break")[1])
        self.lines_tree.bind("<Control-c>", lambda e: (self.copy_selected_lines_to_clipboard(), "break")[1])
        self.lines_tree.bind("<Control-v>", lambda e: (self.paste_lines_from_clipboard(), "break")[1])
        self.lines_tree.bind("<Shift-Insert>", lambda e: (self.paste_lines_from_clipboard(), "break")[1])
        self.lines_tree.bind("<Control-Shift-V>", lambda e: (self.paste_lines_from_clipboard(), "break")[1])
        self.lines_tree.bind("<Control-a>", lambda e: (self._select_all_lines(), "break")[1])
        self.lines_tree.bind("<Insert>", lambda e: (self.add_line(), "break")[1])
        quick_widgets = [
            self.quick_category_combo,
            self.quick_description_entry,
            self.quick_unit_combo,
            self.quick_quantity_entry,
            self.quick_price_entry,
            self.quick_discount_entry,
            self.quick_code_entry,
        ]

        def focus_next(widget: tk.Widget) -> Callable[[tk.Event], str]:
            def handler(event: tk.Event) -> str:
                widget.focus_set()
                return "break"

            return handler

        def add_quick_line_handler(event: tk.Event) -> str:
            self.add_quick_line()
            return "break"

        def clear_quick_line_handler(event: tk.Event) -> str:
            self.clear_quick_line(keep_category=True, keep_unit=True, focus=True)
            return "break"

        def paste_quick_form_handler(event: tk.Event) -> str | None:
            return self._smart_paste_quick_form(event)

        for idx, widget in enumerate(quick_widgets):
            if idx < len(quick_widgets) - 1:
                widget.bind("<Return>", focus_next(quick_widgets[idx + 1]))
                widget.bind("<KP_Enter>", focus_next(quick_widgets[idx + 1]))
            else:
                widget.bind("<Return>", add_quick_line_handler)
                widget.bind("<KP_Enter>", add_quick_line_handler)
            widget.bind("<Control-Return>", add_quick_line_handler)
            widget.bind("<Control-KP_Enter>", add_quick_line_handler)
            widget.bind("<Escape>", clear_quick_line_handler)
            widget.bind("<Control-v>", paste_quick_form_handler)
            widget.bind("<Shift-Insert>", paste_quick_form_handler)
            widget.bind("<Control-Shift-V>", lambda e: (self.paste_clipboard_into_quick_form(), "break")[1])

    def _build_payments_tab(self) -> None:
        outer = ttk.Frame(self.payments_tab, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        toolbar = ttk.Frame(outer, style="App.TFrame")
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="Dodaj uplatu", style="Primary.TButton", command=self.add_payment).pack(side="left")
        ttk.Button(toolbar, text="Povraćaj / odobrenje", command=self.add_payment_refund).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Obriši uplatu", command=self.delete_payment).pack(side="left", padx=4)
        cols = ("date", "amount", "method", "note")
        self.payments_tree = ttk.Treeview(outer, columns=cols, show="headings")
        setup_treeview_tree(self.payments_tree)
        for key, title, width, anchor in [
            ("date", "Datum", 100, "w"),
            ("amount", "Iznos", 120, "e"),
            ("method", "Način", 150, "w"),
            ("note", "Napomena", 500, "w"),
        ]:
            self.payments_tree.heading(key, text=title)
            self.payments_tree.column(key, width=width, anchor=anchor)
        self.payments_tree.pack(fill="both", expand=True)

    def _build_attachments_tab(self) -> None:
        outer = ttk.Frame(self.attachments_tab, style="App.TFrame")
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        toolbar = ttk.Frame(outer, style="App.TFrame")
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="Tip").pack(side="left")
        self.attachment_type_combo = ttk.Combobox(toolbar, textvariable=self.attachment_type_var, values=ATTACHMENT_TYPE_OPTIONS, width=16, state="readonly", style="Modern.TCombobox")
        self.attachment_type_combo.pack(side="left", padx=6)
        ttk.Button(toolbar, text="Dodaj prilog", style="Primary.TButton", command=self.add_attachment).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Otvori", command=self.open_selected_attachment).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Obriši prilog", command=self.delete_attachment).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Otvori folder", command=self.open_attachments_folder).pack(side="left", padx=4)
        cols = ("type", "name", "size", "date", "path")
        self.attachments_tree = ttk.Treeview(outer, columns=cols, show="headings")
        setup_treeview_tree(self.attachments_tree)
        for key, title, width, anchor in [
            ("type", "Tip", 150, "w"),
            ("name", "Naziv", 280, "w"),
            ("size", "Veličina", 90, "e"),
            ("date", "Datum", 130, "w"),
            ("path", "Putanja", 660, "w"),
        ]:
            self.attachments_tree.heading(key, text=title)
            self.attachments_tree.column(key, width=width, anchor=anchor)
        self.attachments_tree.pack(fill="both", expand=True)
        self.attachments_tree.bind("<Double-1>", lambda e: self.open_selected_attachment())

    def _load_lists(self, selected_customer_id: int | None = None, selected_project_id: int | None = None) -> None:
        customers = self.app.db.list_customers()
        self.customer_map = {"": 0}
        customer_values = [""]
        for row in customers:
            display = f'{row["name"]} [{row["id"]}]'
            customer_values.append(display)
            self.customer_map[display] = row["id"]
        if self.customer_combo is not None:
            self.customer_combo["values"] = customer_values

        # A project can have invoices for many different customers. Never filter the
        # project selector by the currently selected invoice customer.
        projects = self.app.db.list_projects()
        if selected_project_id:
            project_ids = {row["id"] for row in projects}
            if selected_project_id not in project_ids:
                extra = self.app.db.get_project(selected_project_id)
                if extra:
                    projects.append(extra)
        self.project_map = {"": 0}
        project_values = [""]
        for row in projects:
            display = f'{row["name"]} [{row["id"]}]'
            project_values.append(display)
            self.project_map[display] = row["id"]
        if self.project_combo is not None:
            self.project_combo["values"] = project_values

        if self.customer_combo is not None and self.vars["customer_id"].get().strip() not in customer_values:
            self.vars["customer_id"].set("")
        if self.project_combo is not None and self.vars["project_id"].get().strip() not in project_values:
            self.vars["project_id"].set("")

    def _load_invoice_templates(self, selected_template_id: int | None = None) -> None:
        """List active forms; an archived form remains selectable only on its old invoice."""
        current_template_id = selected_template_id
        if current_template_id is None:
            current_template_id = self.template_map.get(self.vars["invoice_template_id"].get().strip())
        templates = self.db.list_invoice_templates(include_archived=current_template_id is not None)
        self.template_map = {}
        values: list[str] = []
        can_use_custom_templates = self.app.plan_includes_feature("custom_invoice_templates")
        for template in templates:
            template_id = int(template["id"])
            if int(template.get("archived") or 0) and template_id != current_template_id:
                continue
            if template_id > 0 and not can_use_custom_templates and template_id != current_template_id:
                continue
            suffix = " (arhiviran)" if int(template.get("archived") or 0) else ""
            display = f'{template.get("name") or "Obrazac"} [{template_id}]{suffix}'
            values.append(display)
            self.template_map[display] = template_id
        if self.invoice_template_combo is not None:
            self.invoice_template_combo["values"] = values
        chosen_id = current_template_id if current_template_id is not None else self.db.default_invoice_template_id()
        self.vars["invoice_template_id"].set(self._display_value_for_id(self.template_map, chosen_id))

    def _selected_template_id(self) -> int:
        selected = self.vars["invoice_template_id"].get().strip()
        template_id = self.template_map.get(selected)
        if template_id is not None:
            return int(template_id)
        return int(self.db.default_invoice_template_id() or 0)

    def _manage_invoice_templates(self) -> None:
        dialog = InvoiceTemplateDialog(self, self.app)
        self.wait_window(dialog)
        self._load_invoice_templates(selected_template_id=self._selected_template_id())

    def _open_selected_invoice_template(self) -> None:
        try:
            open_path(self.db.invoice_template_path(self._selected_template_id()))
        except Exception as exc:
            messagebox.showerror("Šabloni fakture", str(exc), parent=self)

    def _display_value_for_id(self, mapping: dict[str, int], target_id: int | None) -> str:
        if target_id is None:
            return ""
        for key, value in mapping.items():
            if value == target_id:
                return key
        return ""

    def _set_customer_selection(self, customer_id: int | None, *, refresh_projects: bool = True) -> None:
        if self.customer_combo is not None:
            self.vars["customer_id"].set(self._display_value_for_id(self.customer_map, customer_id))

    def _set_project_selection(self, project_id: int | None) -> None:
        if self.project_combo is not None:
            self.vars["project_id"].set(self._display_value_for_id(self.project_map, project_id))

    def _load_invoice(self) -> None:
        invoice = self.db.get_invoice(self.invoice_id)
        if not invoice:
            messagebox.showerror("Greška", "Faktura nije pronađena.")
            self.destroy()
            return
        self.vars["invoice_number"].set(tr(str(invoice.get("invoice_number", ""))))
        self.vars["status_code"].set(localized_status_label(invoice.get("status_code", "draft")))
        self.vars["invoice_kind"].set(INVOICE_KIND_LABELS.get(str(invoice.get("invoice_kind") or "standard"), INVOICE_KIND_LABELS["standard"]))
        self.vars["advance_source_invoice_id"].set(str(invoice.get("advance_source_invoice_id") or ""))
        self.vars["issue_date"].set(display_date(invoice.get("issue_date")))
        self.vars["tax_event_date"].set(display_date(invoice.get("tax_event_date")))
        self.vars["due_date"].set(display_date(invoice.get("due_date")))
        self.vars["currency"].set(invoice.get("currency", DEFAULT_CURRENCY))
        self.vars["document_language"].set(
            INVOICE_DOCUMENT_LANGUAGE_LABELS.get(str(invoice.get("document_language") or "").lower(), "")
        )
        self.vars["payment_method"].set(invoice.get("payment_method", payment_method_default()))
        self.vars["issue_place"].set(invoice.get("issue_place", ""))
        self.vars["project_name"].set(invoice.get("project_name", ""))
        self.vars["site_address"].set(invoice.get("site_address", ""))
        self.vars["contract_no"].set(invoice.get("contract_no", ""))
        self.vars["protocol_no"].set(invoice.get("protocol_no", ""))
        self.vars["period_from"].set(display_date(invoice.get("period_from")))
        self.vars["period_to"].set(display_date(invoice.get("period_to")))
        self.vars["order_reference"].set(invoice.get("order_reference", ""))
        self.vars["customer_name"].set(invoice.get("customer_name", ""))
        self.vars["customer_eik"].set(invoice.get("customer_eik", ""))
        self.vars["customer_vat"].set(invoice.get("customer_vat", ""))
        self.vars["customer_address"].set(invoice.get("customer_address", ""))
        self.vars["customer_contact"].set(invoice.get("customer_contact", ""))
        self.vars["customer_phone"].set(invoice.get("customer_phone", ""))
        self.vars["customer_email"].set(invoice.get("customer_email", ""))
        self.vars["customer_payment_term_days"].set(str(invoice.get("customer_payment_term_days", DEFAULT_PAYMENT_TERM_DAYS)))
        self.vars["discount_total"].set(str(invoice.get("discount_total", 0)))
        self.vars["retention_percent"].set(str(float(invoice.get("retention_percent", 0)) * 100 if float(invoice.get("retention_percent", 0)) <= 1 else invoice.get("retention_percent", 0)))
        self.vars["advance_amount"].set(str(invoice.get("advance_amount", 0)))
        self._load_lists(selected_customer_id=invoice.get("customer_id"), selected_project_id=invoice.get("project_id"))
        self._load_invoice_templates(selected_template_id=int(invoice.get("invoice_template_id") or 0))
        self._set_default_document_language()
        # Load the option lists first. Selecting before the lists exist cleared
        # the project field and prevented preview/export for saved invoices.
        self._set_customer_selection(invoice.get("customer_id"), refresh_projects=False)
        self._set_project_selection(invoice.get("project_id"))
        self._on_invoice_kind_changed()
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", invoice.get("note", ""))
        self.item_data = invoice.get("items", [])
        self.payment_rows = invoice.get("payments", [])
        self.attachment_rows = invoice.get("attachments", [])
        self._refresh_treeviews()
        self._refresh_totals()

    def _load_correction_draft(self, source_invoice_id: int) -> None:
        """Copy an issued EUR invoice into a new, unnumbered correction draft."""
        try:
            source = self.db.prepare_invoice_correction_draft(source_invoice_id)
        except ValueError as exc:
            messagebox.showerror("Ispravka fakture", str(exc), parent=self)
            self.destroy()
            return

        today = date.today()
        term_days = int(source.get("customer_payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)
        self.title("Ispravka fakture")
        self.vars["invoice_number"].set(tr("Broj će biti dodeljen pri čuvanju"))
        self.vars["status_code"].set(localized_status_label("draft"))
        self.vars["invoice_kind"].set(INVOICE_KIND_LABELS.get(str(source.get("invoice_kind") or "standard"), INVOICE_KIND_LABELS["standard"]))
        self.vars["advance_source_invoice_id"].set(str(source.get("advance_source_invoice_id") or ""))
        self.vars["issue_date"].set(today.strftime("%d.%m.%Y"))
        self.vars["tax_event_date"].set(today.strftime("%d.%m.%Y"))
        self.vars["due_date"].set((today + timedelta(days=term_days)).strftime("%d.%m.%Y"))
        self.vars["currency"].set(str(source.get("currency") or self.app.company.get("default_currency") or DEFAULT_CURRENCY))
        self.vars["document_language"].set(
            INVOICE_DOCUMENT_LANGUAGE_LABELS.get(str(source.get("document_language") or "").lower(), "")
        )
        self._set_default_document_language()
        self.vars["payment_method"].set(str(source.get("payment_method") or payment_method_default()))
        self.vars["issue_place"].set(str(source.get("issue_place") or ""))
        self.vars["project_name"].set(str(source.get("project_name") or ""))
        self.vars["site_address"].set(str(source.get("site_address") or ""))
        self.vars["contract_no"].set(str(source.get("contract_no") or ""))
        self.vars["protocol_no"].set(str(source.get("protocol_no") or ""))
        self.vars["period_from"].set(display_date(source.get("period_from")))
        self.vars["period_to"].set(display_date(source.get("period_to")))
        self.vars["order_reference"].set(str(source.get("order_reference") or ""))
        self.vars["customer_name"].set(str(source.get("customer_name") or ""))
        self.vars["customer_eik"].set(str(source.get("customer_eik") or ""))
        self.vars["customer_vat"].set(str(source.get("customer_vat") or ""))
        self.vars["customer_address"].set(str(source.get("customer_address") or ""))
        self.vars["customer_contact"].set(str(source.get("customer_contact") or ""))
        self.vars["customer_phone"].set(str(source.get("customer_phone") or ""))
        self.vars["customer_email"].set(str(source.get("customer_email") or ""))
        self.vars["customer_payment_term_days"].set(str(term_days))
        self.vars["discount_total"].set(str(source.get("discount_total") or 0))
        retention = float(source.get("retention_percent") or 0)
        self.vars["retention_percent"].set(str(retention * 100 if retention <= 1 else retention))
        self.vars["advance_amount"].set(str(source.get("advance_amount") or 0))
        self._load_lists(
            selected_customer_id=source.get("customer_id"),
            selected_project_id=source.get("project_id"),
        )
        self._load_invoice_templates(selected_template_id=int(source.get("invoice_template_id") or 0))
        self._set_customer_selection(source.get("customer_id"), refresh_projects=False)
        self._set_project_selection(source.get("project_id"))
        self._on_invoice_kind_changed()
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", f"Ispravka fakture {source.get('invoice_number')}.\n")
        item_keys = (
            "category", "description", "unit", "quantity", "unit_price",
            "discount_percent", "code_stage",
        )
        self.item_data = [
            {key: item.get(key, "") for key in item_keys}
            for item in source.get("items", [])
        ]
        self.payment_rows = []
        self.attachment_rows = []
        self._refresh_treeviews()
        self._refresh_totals()

    def _on_customer_selected(self) -> None:
        customer_id = self._selected_customer_id()
        if not customer_id:
            self._load_lists(selected_customer_id=None)
            return
        customer = self.db.get_customer(customer_id)
        self.vars["customer_name"].set(customer.get("name", ""))
        self.vars["customer_eik"].set(customer.get("eik", ""))
        self.vars["customer_vat"].set(customer.get("vat_number", ""))
        self.vars["customer_address"].set(customer.get("address", ""))
        self.vars["customer_contact"].set(customer.get("contact_person", ""))
        self.vars["customer_phone"].set(customer.get("phone", ""))
        self.vars["customer_email"].set(customer.get("email", ""))
        self.vars["customer_payment_term_days"].set(str(customer.get("payment_term_days", DEFAULT_PAYMENT_TERM_DAYS)))
        self._sync_due_date()
        self._load_advance_sources()

    def _on_project_selected(self) -> None:
        project_id = self._selected_project_id()
        if not project_id:
            return
        project = self.db.get_project(project_id)
        self.vars["project_name"].set(project.get("name", ""))
        self.vars["site_address"].set(project.get("site_address", ""))
        self.vars["contract_no"].set(project.get("contract_no", ""))
        self.vars["contract_net_amount"].set(str(project.get("contract_net_amount") or 0))
        self.vars["advance_percent"].set(str(project.get("advance_percent") or 0))
        self.vars["protocol_no"].set(project.get("protocol_no", ""))
        self.vars["period_from"].set(display_date(project.get("period_from")))
        self.vars["period_to"].set(display_date(project.get("period_to")))
        self.vars["order_reference"].set(project.get("order_reference", ""))
        self._load_advance_sources()
        self._set_advance_line_mode(self._selected_invoice_kind() == "advance")
        self._refresh_treeviews()
        self._refresh_totals()

    def _advance_display_items(self) -> list[dict[str, Any]]:
        if self._selected_invoice_kind() != "advance":
            return self.item_data
        project_id = self._selected_project_id()
        if not project_id:
            return []
        try:
            return [self._line_from_payload(self.db.project_advance_invoice_item(project_id))]
        except ValueError:
            return []

    def _selected_invoice_kind(self) -> str:
        selected = canonical_ui_text(self.vars["invoice_kind"].get().strip(), active_ui_language())
        for code, label in INVOICE_KIND_LABELS.items():
            if selected == label:
                return code
        return "standard"

    def _selected_advance_source_id(self) -> int | None:
        value = self.vars["advance_source_invoice_id"].get().strip()
        return self.advance_source_map.get(value) if value in self.advance_source_map else None

    def _load_advance_sources(self) -> None:
        if self.advance_source_combo is None:
            return
        if self._selected_invoice_kind() != "final":
            self.advance_source_map = {}
            self.advance_source_combo["values"] = []
            self.vars["advance_source_invoice_id"].set("")
            return
        project_id = self._selected_project_id()
        if not project_id:
            self.advance_source_map = {}
            self.advance_source_combo["values"] = []
            self.vars["advance_source_invoice_id"].set("")
            return
        selected_id = self._selected_advance_source_id()
        if selected_id is None and self.vars["advance_source_invoice_id"].get().strip().isdigit():
            selected_id = int(self.vars["advance_source_invoice_id"].get().strip())
        rows = self.db.list_available_advance_invoices(
            project_id=project_id,
            customer_id=self._selected_customer_id(),
            include_invoice_id=self.invoice_id,
        )
        self.advance_source_map = {}
        values: list[str] = []
        for row in rows:
            display = (
                f"{row.get('invoice_number') or tr('Avans')} | "
                f"{tr('plaćeno')} {fmt_money(row.get('paid_total') or 0, row.get('currency') or DEFAULT_CURRENCY)} | "
                f"{display_date(row.get('issue_date'))}"
            )
            values.append(display)
            self.advance_source_map[display] = int(row["id"])
        self.advance_source_combo["values"] = values
        chosen = next((value for value, source_id in self.advance_source_map.items() if source_id == selected_id), "")
        self.vars["advance_source_invoice_id"].set(chosen)
        if chosen:
            self._on_advance_source_selected()

    def _on_invoice_kind_changed(self) -> None:
        kind = self._selected_invoice_kind()
        if self.advance_amount_entry is not None:
            self.advance_amount_entry.configure(state="readonly" if kind == "final" else "normal")
        if kind == "advance":
            self.vars["advance_amount"].set("0")
        self._set_advance_line_mode(kind == "advance")
        self._load_advance_sources()
        self._refresh_treeviews()
        self._refresh_totals()

    def _set_advance_line_mode(self, is_advance: bool) -> None:
        """Keep an agreement advance out of ordinary labour/material lines."""
        if self.lines_quick_frame is None or self.lines_toolbar is None or self.advance_lines_notice is None:
            return
        if is_advance:
            project_id = self._selected_project_id()
            notice = tr("Izaberite projekat sa vrednošću ugovora bez PDV-a i procentom avansa.")
            if project_id:
                try:
                    terms = self.db.project_advance_terms(project_id)
                    currency = self.vars["currency"].get() or DEFAULT_CURRENCY
                    notice = (
                        f"{tr('Ugovor bez PDV-a')}: {fmt_money(terms['contract_net_amount'], currency)} | "
                        f"{tr('Avans')}: {terms['advance_percent']:g}% | "
                        f"{tr('Avans bez PDV-a')}: {fmt_money(terms['advance_net_amount'], currency)}. "
                        f"{tr('OpsNest automatski pravi ugovornu avansnu stavku pri pregledu i čuvanju.')}"
                    )
                except ValueError as exc:
                    notice = str(exc)
            self.advance_lines_notice_var.set(notice)
            self.lines_quick_frame.pack_forget()
            self.lines_toolbar.pack_forget()
            self.advance_lines_notice.pack(fill="x", pady=(0, 8), before=self.lines_tree)
            return
        self.advance_lines_notice.pack_forget()
        self.lines_quick_frame.pack(fill="x", pady=(0, 8), before=self.lines_toolbar)
        self.lines_toolbar.pack(fill="x", pady=(0, 6), before=self.lines_tree)

    def _ensure_regular_invoice_lines(self) -> bool:
        if self._selected_invoice_kind() != "advance":
            return True
        messagebox.showinfo(
            tr("Ugovorni avans"),
            tr("Avans se obračunava iz ugovora projekta i ne unosi se kroz stavke rada, materijala ili ostalo."),
            parent=self,
        )
        return False

    def _on_advance_source_selected(self) -> None:
        source_id = self._selected_advance_source_id()
        if not source_id:
            return
        source = self.db.get_invoice(source_id)
        if not source:
            return
        self.vars["advance_amount"].set(str(source.get("paid_total") or 0))
        self._refresh_totals()

    def _sync_due_date(self) -> None:
        issue_date = parse_date(self.vars["issue_date"].get()) or date.today()
        try:
            term_days = int(self.vars["customer_payment_term_days"].get() or DEFAULT_PAYMENT_TERM_DAYS)
        except ValueError:
            term_days = DEFAULT_PAYMENT_TERM_DAYS
        self.vars["due_date"].set((issue_date + timedelta(days=term_days)).strftime("%d.%m.%Y"))

    def create_customer_from_invoice(self) -> None:
        initial = {
            "name": self.vars["customer_name"].get().strip(),
            "eik": self.vars["customer_eik"].get().strip(),
            "vat_number": self.vars["customer_vat"].get().strip(),
            "address": self.vars["customer_address"].get().strip(),
            "contact_person": self.vars["customer_contact"].get().strip(),
            "phone": self.vars["customer_phone"].get().strip(),
            "email": self.vars["customer_email"].get().strip(),
            "payment_term_days": self.vars["customer_payment_term_days"].get().strip() or str(DEFAULT_PAYMENT_TERM_DAYS),
            "note": "",
        }
        fields = [
            ("name", "Naziv firme", "entry", ""),
            ("eik", "EIK / BULSTAT", "entry", ""),
            ("vat_number", "PDV broj", "entry", ""),
            ("address", "Adresa", "entry", ""),
            ("contact_person", "Odgovorno lice", "entry", ""),
            ("phone", "Telefon", "entry", ""),
            ("email", "E-mail", "entry", ""),
            ("payment_term_days", "Rok plaćanja (dani)", "entry", str(DEFAULT_PAYMENT_TERM_DAYS)),
            ("note", "Napomena", "text", ""),
        ]

        def on_save(payload: dict[str, Any]) -> bool:
            try:
                payload["payment_term_days"] = int(payload.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)
            except ValueError:
                messagebox.showerror("Greška", "Rok plaćanja mora biti broj.")
                return False
            customer_id = self.db.save_customer(payload)
            self._set_customer_selection(customer_id)
            self._on_customer_selected()
            return True

        EntityLineDialog(self, "Novi kupac", fields, on_save, initial=initial)

    def create_project_from_invoice(self) -> None:
        customer_id = self._selected_customer_id()
        initial = {
            "name": self.vars["project_name"].get().strip(),
            "site_address": self.vars["site_address"].get().strip(),
            "contract_no": self.vars["contract_no"].get().strip(),
            "contract_net_amount": self.vars["contract_net_amount"].get().strip() or "0",
            "advance_percent": self.vars["advance_percent"].get().strip() or "0",
            "protocol_no": self.vars["protocol_no"].get().strip(),
            "period_from": self.vars["period_from"].get().strip(),
            "period_to": self.vars["period_to"].get().strip(),
            "order_reference": self.vars["order_reference"].get().strip(),
            "note": "",
        }
        fields = [
            ("name", "Naziv projekta", "entry", ""),
            ("site_address", "Adresa gradilišta", "entry", ""),
            ("contract_no", "Broj ugovora", "entry", ""),
            ("contract_net_amount", "Vrednost ugovora bez PDV-a", "entry", "0"),
            ("advance_percent", "Avans po ugovoru (%)", "entry", "0"),
            ("protocol_no", "Broj protokola / Akta 19", "entry", ""),
            ("period_from", "Period od", "entry", ""),
            ("period_to", "Period do", "entry", ""),
            ("order_reference", "Referenca", "entry", ""),
            ("note", "Napomena", "text", ""),
        ]

        def on_save(payload: dict[str, Any]) -> bool:
            # Project ownership is independent from an invoice recipient. Keeping an
            # optional legacy customer here does not constrain future invoice buyers.
            for key, label in (("contract_net_amount", "Vrednost ugovora bez PDV-a"), ("advance_percent", "Procenat avansa")):
                raw_value = str(payload.get(key, "")).strip()
                parsed_value = parse_clipboard_number(raw_value)
                if raw_value and parsed_value is None:
                    messagebox.showerror("Greška", f"{label} mora biti broj.", parent=self)
                    return False
                payload[key] = parsed_value or 0
            payload["customer_id"] = customer_id or None
            try:
                project_id = self.db.save_project(payload)
            except ValueError as exc:
                messagebox.showerror("Greška", str(exc), parent=self)
                return False
            self._load_lists(selected_project_id=project_id)
            self._set_project_selection(project_id)
            self._on_project_selected()
            return True

        EntityLineDialog(self, "Novi projekat", fields, on_save, initial=initial)

    def _refresh_treeviews(self) -> None:
        for tree in (self.lines_tree, self.payments_tree, self.attachments_tree):
            for item in tree.get_children():
                tree.delete(item)
        for idx, item in enumerate(self._advance_display_items(), start=1):
            self.lines_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    idx,
                    item.get("category", ""),
                    item.get("description", ""),
                    item.get("unit", ""),
                    f"{float(item.get('quantity', 0)):.2f}",
                    f"{float(item.get('unit_price', 0)):.2f}",
                    f"{float(item.get('discount_percent', 0)):.2f}",
                    f"{float(item.get('net_amount', 0)):.2f}",
                    f"{float(item.get('vat_amount', 0)):.2f}",
                    f"{float(item.get('gross_amount', 0)):.2f}",
                    item.get("code_stage", ""),
                ),
                tags=(tree_row_tag(len(self.lines_tree.get_children())),),
            )
        for idx, row in enumerate(self.payment_rows, start=1):
            self.payments_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    display_date(row.get("payment_date")),
                    fmt_money(row.get("amount"), self.vars["currency"].get()),
                    row.get("method", ""),
                    row.get("note", ""),
                ),
                tags=(tree_row_tag(len(self.payments_tree.get_children())),),
            )
        for row in self.attachment_rows:
            stored_path_text = str(row.get("stored_path", "") or "")
            stored_path = Path(stored_path_text) if stored_path_text else None
            size_text = format_file_size(stored_path.stat().st_size) if stored_path and stored_path.exists() else ""
            self.attachments_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row.get("attachment_type", ""),
                    row.get("original_name", ""),
                    size_text,
                    row.get("created_at", "")[:19].replace("T", " "),
                    row.get("stored_path", ""),
                ),
                tags=(tree_row_tag(len(self.attachments_tree.get_children())),),
            )

    def _selected_customer_id(self) -> int | None:
        value = self.vars["customer_id"].get().strip()
        return self.customer_map.get(value) if value in self.customer_map else None

    def _selected_project_id(self) -> int | None:
        value = self.vars["project_id"].get().strip()
        return self.project_map.get(value) if value in self.project_map else None

    def _apply_customer_terms(self) -> None:
        customer_id = self._selected_customer_id()
        if not customer_id:
            return
        customer = self.db.get_customer(customer_id)
        self.vars["customer_name"].set(customer.get("name", ""))
        self.vars["customer_eik"].set(customer.get("eik", ""))
        self.vars["customer_vat"].set(customer.get("vat_number", ""))
        self.vars["customer_address"].set(customer.get("address", ""))
        self.vars["customer_contact"].set(customer.get("contact_person", ""))
        self.vars["customer_phone"].set(customer.get("phone", ""))
        self.vars["customer_email"].set(customer.get("email", ""))
        self.vars["customer_payment_term_days"].set(str(customer.get("payment_term_days", DEFAULT_PAYMENT_TERM_DAYS)))
        self._sync_due_date()

    def _refresh_totals(self) -> None:
        try:
            vat_rate = float(self.app.company.get("default_vat_rate", 0.20) or 0.20)
            discount_total = parse_clipboard_number(self.vars["discount_total"].get() or "0")
            retention_input = parse_clipboard_number(self.vars["retention_percent"].get() or "0")
            advance_amount = parse_clipboard_number(self.vars["advance_amount"].get() or "0")
            if discount_total is None or retention_input is None or advance_amount is None:
                raise ValueError("Neispravan broj")
            totals = calculate_invoice_totals(
                self._advance_display_items(),
                vat_rate=vat_rate,
                discount_total=discount_total,
                retention_percent=retention_input / 100.0 if retention_input > 1 else retention_input,
                advance_amount=advance_amount,
                paid_total=sum(float(p.get("amount", 0)) for p in self.payment_rows),
                currency=self.vars["currency"].get() or DEFAULT_CURRENCY,
            )
        except Exception:
            totals = {
                "subtotal": 0,
                "tax_base": 0,
                "vat_total": 0,
                "gross_total": 0,
                "retention_amount": 0,
                "due_before_paid": 0,
                "paid_total": sum(float(p.get("amount", 0)) for p in self.payment_rows),
                "balance_total": 0,
            }
            vat_rate = float(self.app.company.get("default_vat_rate", 0.20) or 0.20)
        self.vars["vat_caption"].set(f"PDV {vat_rate * 100:g}%")
        self.vars["subtotal"].set(fmt_money(totals["subtotal"], self.vars["currency"].get()))
        self.vars["tax_base"].set(fmt_money(totals["tax_base"], self.vars["currency"].get()))
        self.vars["vat_total"].set(fmt_money(totals["vat_total"], self.vars["currency"].get()))
        self.vars["gross_total"].set(fmt_money(totals["gross_total"], self.vars["currency"].get()))
        self.vars["retention_amount"].set(fmt_money(totals["retention_amount"], self.vars["currency"].get()))
        self.vars["due_before_paid"].set(fmt_money(totals["due_before_paid"], self.vars["currency"].get()))
        self.vars["paid_total"].set(fmt_money(totals["paid_total"], self.vars["currency"].get()))
        self.vars["balance_total"].set(fmt_money(totals["balance_total"], self.vars["currency"].get()))
        if not self.vars["invoice_number"].get():
            self.vars["invoice_number"].set(tr("Broj će biti dodeljen pri čuvanju"))

    def clear_quick_line(
        self,
        *,
        keep_category: bool = False,
        keep_unit: bool = False,
        focus: bool = True,
    ) -> None:
        self.quick_vars["category"].set(self.quick_vars["category"].get() if keep_category and self.quick_vars["category"].get() else CATEGORY_OPTIONS[0])
        self.quick_vars["unit"].set(self.quick_vars["unit"].get() if keep_unit and self.quick_vars["unit"].get() else UNIT_OPTIONS[0])
        self.quick_vars["description"].set("")
        self.quick_vars["quantity"].set("")
        self.quick_vars["unit_price"].set("")
        self.quick_vars["discount_percent"].set("0")
        self.quick_vars["code_stage"].set("")
        if focus and self.quick_description_entry is not None:
            self.quick_description_entry.focus_set()

    def _apply_payload_to_quick_form(self, payload: dict[str, Any]) -> None:
        self.quick_vars["category"].set(str(payload.get("category", CATEGORY_OPTIONS[0])) or CATEGORY_OPTIONS[0])
        self.quick_vars["description"].set(str(payload.get("description", "")))
        self.quick_vars["unit"].set(str(payload.get("unit", UNIT_OPTIONS[0])) or UNIT_OPTIONS[0])
        self.quick_vars["quantity"].set(format_clipboard_number(payload.get("quantity", "")))
        self.quick_vars["unit_price"].set(format_clipboard_number(payload.get("unit_price", "")))
        self.quick_vars["discount_percent"].set(format_clipboard_percent(payload.get("discount_percent", "0")) or "0")
        self.quick_vars["code_stage"].set(str(payload.get("code_stage", "")))
        if self.quick_description_entry is not None:
            self.quick_description_entry.focus_set()

    def paste_clipboard_into_quick_form(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Nalepi u formu", "Clipboard je prazan.")
            return
        payloads, _skipped_rows, _has_header = clipboard_payloads_from_text(text)
        if not payloads:
            messagebox.showinfo("Nalepi u formu", "Clipboard ne sadrži red koji mogu da prepoznam.")
            return
        self._apply_payload_to_quick_form(payloads[0])
        if len(payloads) > 1:
            messagebox.showinfo("Nalepi u formu", "U clipboard-u ima više redova. U formu je učitana samo prva stavka.")

    def _smart_paste_quick_form(self, event: tk.Event) -> str | None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return None
        payloads, _skipped_rows, _has_header = clipboard_payloads_from_text(text)
        if not payloads:
            return None
        self._apply_payload_to_quick_form(payloads[0])
        if len(payloads) > 1:
            messagebox.showinfo("Nalepi u formu", "U clipboard-u ima više redova. U formu je učitana samo prva stavka.")
        return "break"

    def _selected_line_indexes(self) -> list[int]:
        indexes: list[int] = []
        for item_id in self.lines_tree.selection():
            try:
                idx = int(item_id) - 1
            except ValueError:
                continue
            if 0 <= idx < len(self.item_data):
                indexes.append(idx)
        return sorted(dict.fromkeys(indexes))

    def _select_all_lines(self) -> None:
        self.lines_tree.selection_set(self.lines_tree.get_children())

    def _line_insert_index(self) -> int:
        indexes = self._selected_line_indexes()
        if indexes:
            return indexes[-1] + 1
        return len(self.item_data)

    def _select_line_indexes(self, indexes: list[int]) -> None:
        if not indexes:
            return
        item_ids = [str(idx + 1) for idx in indexes if 0 <= idx < len(self.item_data) and self.lines_tree.exists(str(idx + 1))]
        if not item_ids:
            return
        self.lines_tree.selection_set(item_ids)
        self.lines_tree.focus(item_ids[0])
        self.lines_tree.see(item_ids[0])

    def _clipboard_header_map(self, values: list[str]) -> dict[str, int] | None:
        header_map: dict[str, int] = {}
        for idx, cell in enumerate(values):
            token = normalize_clipboard_token(cell)
            if not token:
                continue
            for field, aliases in CLIPBOARD_HEADER_ALIASES.items():
                if field in header_map:
                    continue
                for alias in aliases:
                    alias_token = normalize_clipboard_token(alias)
                    if alias_token and (token == alias_token or token.startswith(alias_token) or alias_token in token):
                        header_map[field] = idx
                        break
        return header_map if len(header_map) >= 2 else None

    def _finalize_clipboard_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        payload["category"] = str(payload.get("category", "")).strip() or CATEGORY_OPTIONS[0]
        payload["description"] = str(payload.get("description", "")).strip()
        payload["unit"] = str(payload.get("unit", "")).strip() or UNIT_OPTIONS[0]
        payload["quantity"] = format_clipboard_number(payload.get("quantity", ""))
        payload["unit_price"] = format_clipboard_number(payload.get("unit_price", ""))
        payload["discount_percent"] = format_clipboard_percent(payload.get("discount_percent", ""))
        payload["code_stage"] = str(payload.get("code_stage", "")).strip()
        if not payload["description"]:
            return None
        if not payload["quantity"] or not payload["unit_price"]:
            return None
        if not payload["discount_percent"]:
            payload["discount_percent"] = "0"
        return payload

    def _payload_from_clipboard_values(self, values: list[str], header_map: dict[str, int] | None = None) -> dict[str, Any] | None:
        cleaned = [cell.strip() for cell in values]
        if not any(cleaned):
            return None
        if header_map:
            payload = {
                "category": cleaned[header_map["category"]] if "category" in header_map and header_map["category"] < len(cleaned) else "",
                "description": cleaned[header_map["description"]] if "description" in header_map and header_map["description"] < len(cleaned) else "",
                "unit": cleaned[header_map["unit"]] if "unit" in header_map and header_map["unit"] < len(cleaned) else "",
                "quantity": cleaned[header_map["quantity"]] if "quantity" in header_map and header_map["quantity"] < len(cleaned) else "",
                "unit_price": cleaned[header_map["unit_price"]] if "unit_price" in header_map and header_map["unit_price"] < len(cleaned) else "",
                "discount_percent": cleaned[header_map["discount_percent"]] if "discount_percent" in header_map and header_map["discount_percent"] < len(cleaned) else "0",
                "code_stage": cleaned[header_map["code_stage"]] if "code_stage" in header_map and header_map["code_stage"] < len(cleaned) else "",
            }
            return self._finalize_clipboard_payload(payload)
        if cleaned and cleaned[0].isdigit() and len(cleaned) >= 8:
            cleaned = cleaned[1:]
        if len(cleaned) >= 10:
            return self._finalize_clipboard_payload(
                {
                    "category": cleaned[0],
                    "description": cleaned[1],
                    "unit": cleaned[2],
                    "quantity": cleaned[3],
                    "unit_price": cleaned[4],
                    "discount_percent": cleaned[5],
                    "code_stage": cleaned[9],
                }
            )
        if len(cleaned) >= 7:
            if normalize_clipboard_token(cleaned[0]) in {normalize_clipboard_token(value) for value in CATEGORY_OPTIONS}:
                return self._finalize_clipboard_payload(
                    {
                        "category": cleaned[0],
                        "description": cleaned[1],
                        "unit": cleaned[2],
                        "quantity": cleaned[3],
                        "unit_price": cleaned[4],
                        "discount_percent": cleaned[5],
                        "code_stage": cleaned[6],
                    }
                )
            return self._finalize_clipboard_payload(
                {
                    "category": CATEGORY_OPTIONS[0],
                    "description": cleaned[0],
                    "unit": cleaned[1],
                    "quantity": cleaned[2],
                    "unit_price": cleaned[3],
                    "discount_percent": cleaned[4],
                    "code_stage": cleaned[5] if len(cleaned) > 5 else "",
                }
            )
        if len(cleaned) >= 6:
            if normalize_clipboard_token(cleaned[0]) in {normalize_clipboard_token(value) for value in CATEGORY_OPTIONS}:
                return self._finalize_clipboard_payload(
                    {
                        "category": cleaned[0],
                        "description": cleaned[1],
                        "unit": cleaned[2],
                        "quantity": cleaned[3],
                        "unit_price": cleaned[4],
                        "discount_percent": cleaned[5],
                        "code_stage": "",
                    }
                )
            return self._finalize_clipboard_payload(
                {
                    "category": CATEGORY_OPTIONS[0],
                    "description": cleaned[0],
                    "unit": cleaned[1],
                    "quantity": cleaned[2],
                    "unit_price": cleaned[3],
                    "discount_percent": cleaned[4],
                    "code_stage": cleaned[5],
                }
            )
        return None

    def _insert_line_payloads(self, payloads: list[dict[str, Any]], index: int | None = None) -> int:
        if not self._ensure_regular_invoice_lines():
            return 0
        if not payloads:
            return 0
        insert_at = len(self.item_data) if index is None else max(0, min(index, len(self.item_data)))
        lines = [self._line_from_payload(payload) for payload in payloads]
        self.item_data[insert_at:insert_at] = lines
        self._refresh_treeviews()
        self._refresh_totals()
        self._select_line_indexes(list(range(insert_at, insert_at + len(lines))))
        return len(lines)

    def copy_selected_lines_to_clipboard(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        indexes = self._selected_line_indexes()
        if not indexes:
            messagebox.showinfo("Kopiranje", "Izaberite jednu ili više stavki.")
            return
        header = [
            "Broj",
            "Kategorija",
            "Opis",
            "JM",
            "Količina",
            "Cena bez PDV",
            "Popust %",
            "Bez PDV",
            "PDV",
            "Ukupno",
            "Kod / etap",
        ]
        rows = ["\t".join(header)]
        for display_no, idx in enumerate(indexes, start=1):
            item = self.item_data[idx]
            rows.append(
                "\t".join(
                    [
                        str(display_no),
                        str(item.get("category", "")),
                        str(item.get("description", "")),
                        str(item.get("unit", "")),
                        f"{float(item.get('quantity', 0)):.2f}",
                        f"{float(item.get('unit_price', 0)):.2f}",
                        f"{float(item.get('discount_percent', 0)):.2f}",
                        f"{float(item.get('net_amount', 0)):.2f}",
                        f"{float(item.get('vat_amount', 0)):.2f}",
                        f"{float(item.get('gross_amount', 0)):.2f}",
                        str(item.get("code_stage", "")),
                    ]
                )
            )
        self.clipboard_clear()
        self.clipboard_append("\n".join(rows))
        self.update()

    def _quick_payload(self) -> dict[str, Any]:
        payload = {k: v.get().strip() for k, v in self.quick_vars.items()}
        payload["category"] = payload["category"] or CATEGORY_OPTIONS[0]
        payload["unit"] = payload["unit"] or UNIT_OPTIONS[0]
        payload["discount_percent"] = payload["discount_percent"] or "0"
        return payload

    def _append_line_payload(self, payload: dict[str, Any]) -> None:
        self._insert_line_payloads([payload])

    def add_quick_line(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        try:
            payload = self._quick_payload()
            if not payload["description"]:
                messagebox.showinfo("Stavka", "Unesite opis stavke.")
                return
            quantity = parse_clipboard_number(payload["quantity"])
            unit_price = parse_clipboard_number(payload["unit_price"])
            discount = parse_clipboard_number(payload["discount_percent"] or 0)
            if quantity is None or unit_price is None or discount is None:
                raise ValueError
            payload["quantity"] = quantity
            payload["unit_price"] = unit_price
            payload["discount_percent"] = discount
        except ValueError:
            messagebox.showerror("Greška", "Proverite količinu, cenu i popust.")
            return
        self._insert_line_payloads([payload], index=self._line_insert_index())
        self.clear_quick_line(keep_category=True, keep_unit=True, focus=True)

    def load_selected_line_into_quick_form(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        sel = self.lines_tree.selection()
        if not sel:
            messagebox.showinfo("Stavka", "Izaberite stavku.")
            return
        idx = int(sel[0]) - 1
        if idx < 0 or idx >= len(self.item_data):
            return
        line = self.item_data[idx]
        self.quick_vars["category"].set(str(line.get("category", CATEGORY_OPTIONS[0])))
        self.quick_vars["description"].set(str(line.get("description", "")))
        self.quick_vars["unit"].set(str(line.get("unit", UNIT_OPTIONS[0])))
        self.quick_vars["quantity"].set(f"{float(line.get('quantity', 0)):.2f}")
        self.quick_vars["unit_price"].set(f"{float(line.get('unit_price', 0)):.2f}")
        self.quick_vars["discount_percent"].set(f"{float(line.get('discount_percent', 0)):.2f}")
        self.quick_vars["code_stage"].set(str(line.get("code_stage", "")))
        if self.quick_description_entry is not None:
            self.quick_description_entry.focus_set()

    def duplicate_selected_line(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        indexes = self._selected_line_indexes()
        if not indexes:
            messagebox.showinfo("Stavka", "Izaberite stavku.")
            return
        for idx in indexes:
            if 0 <= idx < len(self.item_data):
                self.item_data.append(dict(self.item_data[idx]))
        self._refresh_treeviews()
        self._refresh_totals()

    def _clipboard_rows(self, text: str) -> list[list[str]]:
        return clipboard_rows_from_text(text)

    def paste_lines_from_clipboard(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Nalepiti", "Clipboard je prazan.")
            return
        payloads, skipped_rows, header_map = clipboard_payloads_from_text(text)
        if not payloads:
            messagebox.showinfo("Nalepiti", "Nisam pronašao redove za uvoz.")
            return
        insert_at = self._line_insert_index()
        if not self.item_data or insert_at >= len(self.item_data):
            insert_hint = "na kraj liste"
        else:
            insert_hint = f"iza stavke #{insert_at}"
        preview = ClipboardPreviewDialog(
            self,
            payloads,
            currency=self.vars["currency"].get() or DEFAULT_CURRENCY,
            vat_rate=float(self.app.company.get("default_vat_rate", 0.20) or 0.20),
            skipped_rows=skipped_rows,
            insert_hint=insert_hint,
            header_map=header_map,
        )
        if not preview.confirmed:
            return
        added_count = self._insert_line_payloads(payloads, index=insert_at)
        message = f"Uvezeno {added_count} stavki iz clipboard-a."
        if skipped_rows:
            message += f"\nPreskočeno {skipped_rows} redova bez validnih podataka."
        messagebox.showinfo("Nalepiti", message)

    def add_line(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        def on_save(payload: dict[str, Any]) -> None:
            line = self._line_from_payload(payload)
            self.item_data.append(line)
            self._refresh_treeviews()
            self._refresh_totals()

        LineItemDialog(self, None, on_save)

    def edit_line(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        sel = self.lines_tree.selection()
        if not sel:
            messagebox.showinfo("Stavka", "Izaberite stavku.")
            return
        idx = int(sel[0]) - 1
        initial = self.item_data[idx]

        def on_save(payload: dict[str, Any]) -> None:
            line = self._line_from_payload(payload)
            self.item_data[idx] = line
            self._refresh_treeviews()
            self._refresh_totals()

        LineItemDialog(self, initial, on_save)

    def delete_line(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        indexes = self._selected_line_indexes()
        if not indexes:
            messagebox.showinfo("Stavka", "Izaberite stavku.")
            return
        for idx in sorted(indexes, reverse=True):
            if 0 <= idx < len(self.item_data):
                del self.item_data[idx]
        self._refresh_treeviews()
        self._refresh_totals()

    def import_project_lines(self) -> None:
        if not self._ensure_regular_invoice_lines():
            return
        # Placeholder for later import flow; keeps the button useful without blocking.
        messagebox.showinfo("Uvoz", "Uvoz stavki iz projekta biće dodat u sledećoj verziji.")

    def _line_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        qty = parse_clipboard_number(payload["quantity"])
        price = parse_clipboard_number(payload["unit_price"])
        discount = parse_clipboard_number(payload.get("discount_percent", 0))
        if qty is None or price is None or discount is None:
            raise ValueError("Proverite količinu, cenu i popust.")
        vat_rate = float(self.app.company.get("default_vat_rate", 0.20) or 0.20)
        line = {
            "category": payload["category"],
            "description": payload["description"],
            "unit": payload["unit"],
            "quantity": qty,
            "unit_price": price,
            "discount_percent": discount,
            "code_stage": payload.get("code_stage", ""),
        }
        calc = calculate_invoice_totals([line], vat_rate=vat_rate, discount_total=0, retention_percent=0, advance_amount=0, paid_total=0, currency=self.vars["currency"].get() or DEFAULT_CURRENCY)
        line["net_amount"] = float(calc["subtotal"])
        line["vat_amount"] = float(calc["vat_total"])
        line["gross_amount"] = float(calc["gross_total"])
        return line

    def _attachments_root(self) -> Path:
        if self.invoice_id:
            return self.db.invoice_attachments_dir(self.invoice_id)
        return get_root_dir() / "Prilozi" / "Nesacuvane_fakture"

    def _archive_invoice_outputs(self) -> dict[str, Path]:
        if not self.invoice_id:
            raise ValueError("Faktura još nije sačuvana.")
        return self.app.archive_invoice_outputs(self.invoice_id)

    def add_payment(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("Uplata", "Prvo sačuvajte fakturu.")
            return
        PaymentDialog(self, self.db, self.invoice_id, on_saved=self._reload_invoice_data)

    def add_payment_refund(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("Povraćaj uplate", "Prvo sačuvajte fakturu i evidentirajte uplatu.")
            return
        PaymentDialog(self, self.db, self.invoice_id, on_saved=self._reload_invoice_data, is_refund=True)

    def create_credit_note(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("Odobrenje", "Prvo sačuvajte fakturu i evidentirajte povraćaj uplate.", parent=self)
            return
        try:
            CreditNoteDialog(self, self.app, self.invoice_id, on_saved=self._reload_invoice_data)
        except ValueError as exc:
            messagebox.showerror("Odobrenje", str(exc), parent=self)

    def delete_draft(self) -> None:
        """Remove the whole non-issued working document, never an issued invoice."""
        if not self.invoice_id:
            if messagebox.askyesno(
                "Odustani od nacrta",
                "Ovaj nacrt još nije sačuvan. Odustati od njega bez čuvanja?",
                parent=self,
            ):
                self.grab_release()
                self.destroy()
            return
        invoice = self.db.get_invoice(self.invoice_id)
        if not invoice:
            self.destroy()
            return
        status = str(invoice.get("status_code") or "draft")
        if status not in {"draft", "pending_approval", "approved"}:
            messagebox.showinfo(
                "Obriši nacrt",
                "Izdatu fakturu nije moguće obrisati. Za nju koristite storno ili ispravku.",
                parent=self,
            )
            return
        kind = normalize_invoice_kind(invoice.get("invoice_kind"))
        kind_label = {"advance": "nacrt avansnog računa", "final": "nacrt završnog računa"}.get(kind, "nacrt fakture")
        number = str(invoice.get("invoice_number") or "bez broja")
        if not messagebox.askyesno(
            "Obriši nacrt",
            f"Obrisati {kind_label} {number}?\n\nOvo ne može da se vrati, ali izdata faktura ne može biti obrisana ovim dugmetom.",
            parent=self,
        ):
            return
        try:
            self.db.delete_invoice(self.invoice_id)
        except ValueError as exc:
            messagebox.showerror("Obriši nacrt", str(exc), parent=self)
            return
        self.app.refresh_all()
        self.grab_release()
        self.destroy()

    def delete_payment(self) -> None:
        sel = self.payments_tree.selection()
        if not sel:
            messagebox.showinfo("Uplata", "Izaberite uplatu.")
            return
        pid = int(sel[0])
        if not messagebox.askyesno("Potvrda", "Obrisati uplatu?"):
            return
        try:
            self.db.delete_payment(pid)
        except ValueError as exc:
            messagebox.showerror("Uplata", str(exc), parent=self)
            return
        self._reload_invoice_data()

    def open_invoice_history(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("Istorija fakture", "Istorija postoji nakon prvog čuvanja fakture.")
            return
        InvoiceHistoryDialog(self, self.db, self.invoice_id)

    def open_einvoice_outbox(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("E-faktura outbox", "Outbox postoji nakon prvog čuvanja fakture.", parent=self)
            return
        EInvoiceOutboxDialog(self, self.db, self.invoice_id)

    def open_einvoice_connection(self) -> None:
        """Open the per-company e-invoice connector setup."""
        EInvoiceConnectionDialog(self, self.app.company)

    def open_recurring_template_dialog(self) -> None:
        if not self.app.require_team_permission(
            {"owner", "administrator", "project_manager", "accountant"},
            "pravljenje ponavljajuće fakture",
            parent=self,
        ):
            return
        if not self._selected_project_id():
            messagebox.showinfo("Ponavljajuća faktura", "Izaberite projekat pre čuvanja šablona.", parent=self)
            return
        RecurringInvoiceSetupDialog(self, self)

    def create_correction_draft(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("Ispravka fakture", "Prvo sačuvajte fakturu koju želite da ispravite.")
            return
        invoice = self.db.get_invoice(self.invoice_id)
        if invoice.get("status_code") == "draft":
            messagebox.showinfo("Ispravka fakture", "Ovo je nacrt. Uredite ga direktno, bez pravljenja nove ispravke.")
            return
        source_invoice_id = self.invoice_id
        self.grab_release()
        self.destroy()
        self.app.open_invoice_editor(correction_invoice_id=source_invoice_id)

    def cancel_invoice(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("Storno fakture", "Prvo sačuvajte fakturu.")
            return
        StornoInvoiceDialog(self, self.app, self.invoice_id, on_saved=self._reload_invoice_data)

    def add_attachment(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("Prilog", "Prvo sačuvajte fakturu.")
            return
        paths = filedialog.askopenfilenames(title="Izaberi priloge")
        if not paths:
            return
        attachments_root = self._attachments_root()
        attachments_root.mkdir(parents=True, exist_ok=True)
        attachment_type = self.attachment_type_var.get().strip() or ATTACHMENT_TYPE_OPTIONS[0]
        now = datetime.now().isoformat(timespec="seconds")
        for raw_path in paths:
            src = Path(raw_path)
            if not src.exists():
                continue
            dest = attachments_root / src.name
            if dest.exists() and dest.resolve() != src.resolve():
                stem = src.stem
                suffix = src.suffix
                index = 1
                while dest.exists():
                    dest = attachments_root / f"{stem}_{index}{suffix}"
                    index += 1
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
            else:
                dest = src
            self.db.conn.execute(
                """
                INSERT INTO attachments (invoice_id, attachment_type, original_name, stored_path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.invoice_id, attachment_type, src.name, str(dest), now),
            )
        self.db.conn.commit()
        self._reload_invoice_data()

    def open_selected_attachment(self) -> None:
        sel = self.attachments_tree.selection()
        if not sel:
            messagebox.showinfo("Prilog", "Izaberite prilog.")
            return
        att_id = int(sel[0])
        row = self.db.conn.execute("SELECT stored_path FROM attachments WHERE id = ?", (att_id,)).fetchone()
        stored_path_text = str(row["stored_path"] if row and row["stored_path"] else "")
        if not stored_path_text:
            messagebox.showinfo("Prilog", "Prilog nema sačuvanu putanju.")
            return
        path = Path(stored_path_text)
        if not path.exists():
            messagebox.showwarning("Prilog", "Datoteka ne postoji na disku.")
            return
        open_path(path)

    def delete_attachment(self) -> None:
        sel = self.attachments_tree.selection()
        if not sel:
            messagebox.showinfo("Prilog", "Izaberite prilog.")
            return
        att_id = int(sel[0])
        row = self.db.conn.execute("SELECT stored_path FROM attachments WHERE id = ?", (att_id,)).fetchone()
        if not messagebox.askyesno("Potvrda", "Obrisati prilog?"):
            return
        self.db.conn.execute("DELETE FROM attachments WHERE id = ?", (att_id,))
        self.db.conn.commit()
        stored_path_text = str(row["stored_path"] if row and row["stored_path"] else "")
        if stored_path_text:
            try:
                Path(stored_path_text).unlink(missing_ok=True)
            except Exception:
                pass
        self._reload_invoice_data()

    def open_attachments_folder(self) -> None:
        folder = self._attachments_root()
        folder.mkdir(parents=True, exist_ok=True)
        open_path(folder)

    def _reload_invoice_data(self) -> None:
        if not self.invoice_id:
            return
        self._load_invoice()

    def _collect_invoice_payload(self, status_code: str, *, show_error: bool = True) -> dict[str, Any] | None:
        try:
            discount_total = parse_clipboard_number(self.vars["discount_total"].get() or "0")
            retention_input = parse_clipboard_number(self.vars["retention_percent"].get() or "0")
            advance_amount = parse_clipboard_number(self.vars["advance_amount"].get() or "0")
            if discount_total is None or retention_input is None or advance_amount is None:
                raise ValueError("Popust, zadržavanje i avans moraju biti brojevi.")
            payload = {
                "id": self.invoice_id,
                "status_code": status_code,
                "invoice_kind": self._selected_invoice_kind(),
                "advance_source_invoice_id": self._selected_advance_source_id(),
                "issue_date": self.vars["issue_date"].get().strip(),
                "tax_event_date": self.vars["tax_event_date"].get().strip(),
                "due_date": self.vars["due_date"].get().strip(),
                "customer_id": self._selected_customer_id(),
                "project_id": self._selected_project_id(),
                "invoice_template_id": self._selected_template_id(),
                "document_language": invoice_document_language_code_from_label(self.vars["document_language"].get()),
                "project_name": self.vars["project_name"].get().strip(),
                "site_address": self.vars["site_address"].get().strip(),
                "contract_no": self.vars["contract_no"].get().strip(),
                "protocol_no": self.vars["protocol_no"].get().strip(),
                "period_from": self.vars["period_from"].get().strip(),
                "period_to": self.vars["period_to"].get().strip(),
                "order_reference": self.vars["order_reference"].get().strip(),
                "issue_place": self.vars["issue_place"].get().strip(),
                "currency": self.vars["currency"].get().strip() or DEFAULT_CURRENCY,
                "payment_method": self.vars["payment_method"].get().strip() or payment_method_default(),
                "customer_name": self.vars["customer_name"].get().strip(),
                "customer_eik": self.vars["customer_eik"].get().strip(),
                "customer_vat": self.vars["customer_vat"].get().strip(),
                "customer_address": self.vars["customer_address"].get().strip(),
                "customer_contact": self.vars["customer_contact"].get().strip(),
                "customer_phone": self.vars["customer_phone"].get().strip(),
                "customer_email": self.vars["customer_email"].get().strip(),
                "customer_payment_term_days": int(self.vars["customer_payment_term_days"].get() or DEFAULT_PAYMENT_TERM_DAYS),
                "discount_total": discount_total,
                "retention_percent": retention_input / 100.0 if retention_input > 1 else retention_input,
                "advance_amount": advance_amount,
                "note": self.note_text.get("1.0", "end").strip(),
                "vat_rate": float(self.app.company.get("default_vat_rate", 0.20) or 0.20),
                "exchange_rate": float(self.app.company.get("exchange_rate", DEFAULT_EXCHANGE_RATE) or DEFAULT_EXCHANGE_RATE),
                **self.app.invoice_actor_payload(),
            }
            if not payload["customer_id"] and self.vars["customer_name"].get().strip():
                # Allow saving without a formal customer record.
                payload["customer_name"] = self.vars["customer_name"].get().strip()
            if not payload["project_id"]:
                raise ValueError("Izaberite projekat. Svaka faktura se arhivira i vodi kroz knjigovodstvo tog projekta.")
        except ValueError as exc:
            if show_error:
                messagebox.showerror("Greška", f"Nije moguće pripremiti fakturu: {exc}")
            return None
        return payload

    def preview_invoice(self, format_name: str) -> None:
        payload = self._collect_invoice_payload("draft")
        if payload is None:
            return
        try:
            data = self.db.preview_invoice_export_payload(payload, self.item_data)
            project_id = int(payload["project_id"])
            # A preview is an invoice working copy, so keep it with the project
            # instead of in a global folder that has no project ownership.
            preview_dir = self.db.project_archive_dir(project_id) / "Izlazne_fakture" / "Pregledi"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            preview_dir = preview_dir / stamp
            preview_dir.mkdir(parents=True, exist_ok=True)
            if format_name == "pdf":
                logo = Path(self.app.company.get("logo_path") or LOGO_FILE)
                # Excel creates the PDF from the exact supplied invoice template.
                # Keep that slower COM work off the Tkinter UI thread.
                def preview_complete(out: Path) -> None:
                    open_path(out)
                    messagebox.showinfo(
                        "Pregled fakture",
                        "PDF za štampu je otvoren. Faktura još nije sačuvana u bazi:\n"
                        f"{out}",
                        parent=self.app,
                    )

                template_path = self.db.invoice_template_path(int(data.get("invoice_template_id") or 0))
                self.app.run_pdf_export(
                    title="Priprema pregleda PDF-a",
                    task=lambda: export_invoice_bundle(data, preview_dir, template_path=template_path, logo_path=logo)["pdf"],
                    on_success=preview_complete,
                )
                return
            else:
                out = preview_dir / f"pregled_fakture_{data['invoice_number']}.xlsx"
                export_invoice_xlsx(
                    data,
                    out,
                    template_path=self.db.invoice_template_path(int(data.get("invoice_template_id") or 0)),
                )
            open_path(out)
        except Exception as exc:
            messagebox.showerror("Pregled fakture", f"Pregled nije moguće napraviti:\n{exc}")
            return
        format_label = "PDF za štampu" if format_name == "pdf" else "Excel šablon"
        messagebox.showinfo("Pregled fakture", f"Otvoren je {format_label}. Faktura još nije sačuvana u bazi:\n{out}")

    def save_invoice(self, status_code: str) -> None:
        requested_status = status_code
        existing = self.db.get_invoice(self.invoice_id) if self.invoice_id else None
        if (
            existing
            and str(existing.get("status_code") or "") in {"issued", "partial", "paid", "due"}
            and status_code == "issued"
        ):
            if not messagebox.askyesno(
                "Izmena izdate fakture",
                (
                    f"Menjate postojeću fakturu {existing.get('invoice_number') or '-'}; "
                    "broj ostaje isti i neće se napraviti duplikat.\n\n"
                    "Ako je dokument već poslat kroz SEF ili kupcu, za zakonsku korekciju koristite "
                    "‘Napravi ispravku’ ili ‘Storniraj fakturu’."
                ),
                parent=self,
            ):
                return
        if status_code == "issued" and self.app.invoice_approval_enabled() and not self.app.is_owner_or_administrator():
            if str((existing or {}).get("status_code") or "") != "approved":
                status_code = "pending_approval"
        payload = self._collect_invoice_payload(status_code)
        if payload is None:
            return
        if not self.item_data:
            if not messagebox.askyesno("Prazna faktura", "Faktura nema stavke. Sačuvati ipak?"):
                return
        try:
            invoice_id = self.db.save_invoice(payload, self.item_data)
        except ValueError as exc:
            messagebox.showerror("Faktura", str(exc), parent=self)
            return
        self.invoice_id = invoice_id
        self._load_invoice()
        self.app.refresh_all()
        saved_invoice = self.db.get_invoice(invoice_id)
        saved_status = str(saved_invoice.get("status_code") or status_code)
        if saved_status in {"issued", "partial", "paid", "due"}:
            if not self.app.queue_invoice_output_export(invoice_id):
                messagebox.showwarning(
                    "Faktura je sačuvana",
                    "Podaci fakture su bezbedno sačuvani u bazi, ali PDF nije moguće napraviti. "
                    "Proverite Microsoft Excel i pokušajte ponovo kroz PDF / štampu.",
                    parent=self,
                )
                return
            messagebox.showinfo(
                "Faktura je izdata",
                "Faktura je izdata. Excel i PDF kopija se automatski prave u pozadini i biće smeštene u folder projekta.",
                parent=self,
            )
            return
        labels = {
            "draft": "Nacrt je sačuvan",
            "pending_approval": "Faktura je poslata na proveru",
            "approved": "Faktura je odobrena",
        }
        label = labels.get(saved_status, "Faktura je sačuvana")
        messagebox.showinfo(
            label,
            (
                "Faktura je poslata vlasniku na proveru. PDF i Excel se automatski prave nakon izdavanja."
                if saved_status == "pending_approval" and requested_status == "issued"
                else "Podaci su bezbedno sačuvani. PDF i Excel se automatski prave tek kada fakturu izdate."
            ),
            parent=self,
        )

    def export_pdf(self, *, open_after: bool = False) -> None:
        if not self.invoice_id:
            messagebox.showinfo("PDF / štampa", "Prvo sačuvajte fakturu, pa će se otvoriti popunjeni originalni šablon kao PDF.")
            return
        invoice = self.db.get_invoice(self.invoice_id)
        if str(invoice.get("status_code") or "draft") not in {"issued", "partial", "paid", "due"}:
            messagebox.showinfo("PDF / štampa", "PDF i štampa postaju dostupni kada fakturu izdate. Za nacrt koristite Pregled PDF / štampa.", parent=self)
            return
        if open_after:
            self.app.open_or_generate_invoice_output(self.invoice_id, "pdf")
            return
        self.app.queue_invoice_output_export(self.invoice_id)

    def export_xlsx(self, *, open_after: bool = False) -> None:
        if not self.invoice_id:
            messagebox.showinfo("Excel šablon", "Prvo sačuvajte fakturu.")
            return
        invoice = self.db.get_invoice(self.invoice_id)
        if str(invoice.get("status_code") or "draft") not in {"issued", "partial", "paid", "due"}:
            messagebox.showinfo("Excel šablon", "Excel kopija postaje dostupna kada fakturu izdate. Za nacrt koristite Pregled Excel.", parent=self)
            return
        if open_after:
            self.app.open_or_generate_invoice_output(self.invoice_id, "xlsx")
            return
        self.app.queue_invoice_output_export(self.invoice_id)

    def check_sef_readiness(self) -> None:
        """Show the selected country's local pre-flight; never contacts a government service."""
        if not self.invoice_id:
            messagebox.showinfo(
                "E-faktura provera",
                "Prvo sačuvajte fakturu. E-faktura provera se radi nad sačuvanim dokumentom.",
                parent=self,
            )
            return
        invoice = self.db.invoice_export_payload(self.invoice_id)
        country = str(self.app.company.get("country_code") or "").upper()
        if country == "RS":
            report = sef_readiness(invoice)
            ready_title, error_title = "SEF priprema", "SEF provera — dopuna podataka"
        elif country == "BG":
            report = bulgaria_en16931_readiness(invoice)
            ready_title, error_title = "Bugarska EN 16931 priprema", "Bugarska EN 16931 — dopuna podataka"
        else:
            report = einvoice_readiness(invoice)
            ready_title, error_title = "E-faktura priprema", "E-faktura provera — dopuna podataka"
        title = ready_title if report.is_ready_for_technical_mapping else error_title
        show = messagebox.showinfo if report.is_ready_for_technical_mapping else messagebox.showwarning
        show(title, report.format_for_user(context="E-faktura"), parent=self)

    def export_ubl_draft(self) -> None:
        """Export a clearly marked local UBL draft; never contact the SEF API."""
        if not self.invoice_id:
            messagebox.showinfo(
                "UBL 2.1 nacrt",
                "Prvo sačuvajte i izdate fakturu, zatim pokrenite SEF proveru.",
                parent=self,
            )
            return
        invoice = self.db.invoice_export_payload(self.invoice_id)
        country = str(self.app.company.get("country_code") or "").upper()
        if country == "RS":
            report = sef_readiness(invoice)
        elif country == "BG":
            report = bulgaria_en16931_readiness(invoice)
        else:
            report = einvoice_readiness(invoice)
        if report.errors:
            messagebox.showwarning(
                "UBL 2.1 nacrt",
                report.format_for_user(context="E-faktura"),
                parent=self,
            )
            return
        project_id = int(invoice.get("project_id") or 0)
        if not project_id:
            messagebox.showerror("UBL 2.1 nacrt", "Faktura nema projekat za lokalnu arhivu.", parent=self)
            return
        folder = self.db.project_archive_dir(project_id) / "E_fakture" / "UBL_2_1_nacrti"
        output = folder / f"UBL_2_1_DRAFT_{safe_filename(invoice.get('invoice_number') or str(self.invoice_id))}.xml"
        try:
            export_ubl_21_draft(invoice, output)
            provider = provider_for_country(self.app.company.get("country_code"))
            self.db.register_einvoice_draft(
                self.invoice_id,
                provider_code=provider.code if provider else "generic-ubl",
                country_code=str(self.app.company.get("country_code") or ""),
                document_path=str(output),
                document_hash=hashlib.sha256(output.read_bytes()).hexdigest(),
            )
        except ValueError as exc:
            messagebox.showwarning("UBL 2.1 nacrt", str(exc), parent=self)
            return
        except OSError as exc:
            messagebox.showerror("UBL 2.1 nacrt", f"Nacrt nije moguće sačuvati:\n{exc}", parent=self)
            return
        open_path(output)
        messagebox.showinfo(
            "UBL 2.1 nacrt",
            (
                "Sačuvan je lokalni UBL 2.1 nacrt za Bugarska EN 16931/B2G tehnički pregled. "
                if country == "BG"
                else "Sačuvan je lokalni UBL 2.1 nacrt za tehnički pregled. "
            )
            + "Evidentiran je u e-faktura outbox-u, ali nije validiran za nacionalni sistem "
            "i nije poslat nijednom sistemu.\n\n"
            f"{output}",
            parent=self,
        )

    def test_sef_demo_connection(self) -> None:
        """Confirm a user-supplied demo key without persisting or sending invoices."""
        provider = provider_for_country(self.app.company.get("country_code"))
        if provider is None or not provider.supports_demo_connection:
            bg_message = (
                "Za Bugarsku nema SEF demo API veze. Koristite Poveži e-fakture za BG/EN 16931 uputstvo, "
                "zatim E-faktura proveru i UBL 2.1 nacrt kada ga kupac ili javni naručilac zahteva."
                if provider is not None and provider.country_code == "BG"
                else "Za izabranu državu još nema e-faktura demo konektora. Generički UBL 2.1 nacrt ostaje dostupan za tehnički pregled."
            )
            messagebox.showinfo(
                "SEF demo veza",
                bg_message,
                parent=self,
            )
            return
        api_key = simpledialog.askstring(
            "SEF demo API ključ",
            "Unesite API ključ generisan u SEF demo nalogu.\n"
            "Ključ se ne čuva u OpsNest-u. Provera šalje samo ključ da pročita verziju demo sistema.",
            show="*",
            parent=self,
        )
        if not api_key:
            return
        if not messagebox.askyesno(
            "Potvrda SEF demo provere",
            "Da li želite da OpsNest pošalje samo uneti API ključ na efakturadev.mfin.gov.rs "
            "radi provere verzije sistema? Nijedna faktura niti XML neće biti poslati.",
            parent=self,
        ):
            return

        def worker() -> None:
            try:
                version = get_sef_version(api_key, environment="demo")
            except SefApiError as exc:
                self.after(0, lambda: messagebox.showwarning("SEF demo veza", str(exc), parent=self))
                return
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "SEF demo veza",
                    f"Veza je potvrđena. SEF demo verzija: {version}\n\n"
                    "Nije sačuvana nijedna tajna i nije poslat nijedan dokument.",
                    parent=self,
                ),
            )

        threading.Thread(target=worker, name="opsnest-sef-demo-check", daemon=True).start()

    def send_email(self) -> None:
        if not self.invoice_id:
            messagebox.showinfo("E-mail", "Prvo sačuvajte fakturu.")
            return
        SendEmailDialog(self, self.app, self.invoice_id)


def main() -> int:
    enable_high_dpi()
    app = MainApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
