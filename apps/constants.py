from django.db import models


class District(models.TextChoices):
    # Kigali City
    GASABO = "gasabo", "Gasabo (Kigali)"
    KICUKIRO = "kicukiro", "Kicukiro (Kigali)"
    NYARUGENGE = "nyarugenge", "Nyarugenge (Kigali)"
    # Northern Province
    BURERA = "burera", "Burera (Northern)"
    GAKENKE = "gakenke", "Gakenke (Northern)"
    GICUMBI = "gicumbi", "Gicumbi (Northern)"
    MUSANZE = "musanze", "Musanze (Northern)"
    RULINDO = "rulindo", "Rulindo (Northern)"
    # Southern Province
    GISAGARA = "gisagara", "Gisagara (Southern)"
    HUYE = "huye", "Huye (Southern)"
    KAMONYI = "kamonyi", "Kamonyi (Southern)"
    MUHANGA = "muhanga", "Muhanga (Southern)"
    NYAMAGABE = "nyamagabe", "Nyamagabe (Southern)"
    NYANZA = "nyanza", "Nyanza (Southern)"
    NYARUGURU = "nyaruguru", "Nyaruguru (Southern)"
    RUHANGO = "ruhango", "Ruhango (Southern)"
    # Eastern Province
    BUGESERA = "bugesera", "Bugesera (Eastern)"
    GATSIBO = "gatsibo", "Gatsibo (Eastern)"
    KAYONZA = "kayonza", "Kayonza (Eastern)"
    KIREHE = "kirehe", "Kirehe (Eastern)"
    NGOMA = "ngoma", "Ngoma (Eastern)"
    NYAGATARE = "nyagatare", "Nyagatare (Eastern)"
    RWAMAGANA = "rwamagana", "Rwamagana (Eastern)"
    # Western Province
    KARONGI = "karongi", "Karongi (Western)"
    NGORORERO = "ngororero", "Ngororero (Western)"
    NYABIHU = "nyabihu", "Nyabihu (Western)"
    NYAMASHEKE = "nyamasheke", "Nyamasheke (Western)"
    RUBAVU = "rubavu", "Rubavu (Western)"
    RUSIZI = "rusizi", "Rusizi (Western)"
    RUTSIRO = "rutsiro", "Rutsiro (Western)"


class CeremonyType(models.TextChoices):
    GUSABA = "gusaba", "Gusaba (introduction ceremony)"
    TRADITIONAL = "traditional", "Traditional wedding"
    CIVIL = "civil", "Civil ceremony"
    CHURCH = "church", "Church / religious ceremony"
    RECEPTION = "reception", "Wedding reception"


class PriceStatus(models.TextChoices):
    INDICATIVE = "indicative", "Indicative price (not confirmed by vendor)"
    VENDOR_QUOTED = "vendor_quoted", "Vendor-confirmed quote"
    BOOKED = "booked", "Confirmed booking"
