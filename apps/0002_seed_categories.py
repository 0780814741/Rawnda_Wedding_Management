from django.db import migrations

CATEGORIES = [
    ("Venues", "venue", "Halls, gardens and hotels"),
    ("Tents and furniture", "tent", "Tents, chairs, tables"),
    ("Catering", "catering", "Food and drinks"),
    ("Decoration", "decoration", "Flowers, styling and set-up"),
    ("Photographers", "photographer", "Wedding photography"),
    ("Videographers", "videographer", "Wedding video"),
    ("MCs", "mc", "Masters of ceremonies"),
    ("DJs", "dj", "Music and DJ services"),
    ("Singers", "singer", "Solo singers and choirs"),
    ("Bands", "band", "Live bands"),
    ("Traditional performers", "traditional-performers", "Intore dancers, drummers and traditional troupes"),
    ("Sound and lighting", "sound-lighting", "PA systems and lighting"),
    ("Transport", "transport", "Wedding cars and guest transport"),
    ("Bridal wear", "bridal-wear", "Dresses, suits and traditional attire"),
    ("Makeup and beauty", "makeup", "Makeup, hair and beauty"),
    ("Cakes", "cake", "Wedding cakes and desserts"),
    ("Wedding planners", "planner", "Day-of coordination and planning"),
    ("Other services", "other", "Other wedding services"),
]


def seed(apps, schema_editor):
    Category = apps.get_model("vendors", "VendorCategory")
    for n, (name, slug, desc) in enumerate(CATEGORIES, start=1):
        Category.objects.get_or_create(slug=slug, defaults={"name": name, "description": desc, "ordering": n * 10})


class Migration(migrations.Migration):
    dependencies = [("vendors", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
