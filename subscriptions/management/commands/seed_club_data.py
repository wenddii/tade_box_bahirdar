import io
from datetime import date, timedelta
from decimal import Decimal
from PIL import Image, ImageDraw
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from subscriptions.models import (
    MemberProfile,
    Subscription,
    PaymentSubmission,
    ClubSettings,
    AuditLog
)
from subscriptions.utils import get_current_subscription_month

User = get_user_model()


def generate_sample_receipt(text_title: str, amount_etb: str, tx_id: str) -> ContentFile:
    """Generates a realistic mobile transfer receipt image in memory."""
    img = Image.new('RGB', (450, 600), color=(17, 24, 39))  # Dark slate background
    draw = ImageDraw.Draw(img)

    # Receipt border & header box
    draw.rectangle([(20, 20), (430, 580)], outline=(55, 65, 81), width=2)
    draw.rectangle([(25, 25), (425, 110)], fill=(31, 41, 55))

    # Header text
    draw.text((40, 45), "TELEBIRR / CBE TRANSFER RECEIPT", fill=(239, 68, 68))
    draw.text((40, 75), "Tade Boxing Club Subscription", fill=(243, 244, 246))

    # Divider
    draw.line([(35, 125), (415, 125)], fill=(75, 85, 99), width=1)

    # Body
    draw.text((40, 150), f"Member: {text_title}", fill=(209, 213, 219))
    draw.text((40, 190), f"Amount Paid: {amount_etb} ETB", fill=(52, 211, 153))
    draw.text((40, 230), f"Transaction ID: {tx_id}", fill=(156, 163, 175))
    draw.text((40, 270), "Status: SUCCESSFUL", fill=(34, 197, 94))
    draw.text((40, 310), f"Date: {date.today().strftime('%d %B %Y')}", fill=(156, 163, 175))
    draw.text((40, 350), "Payment Method: CBE Birr / Telebirr", fill=(156, 163, 175))

    # Footer
    draw.line([(35, 480), (415, 480)], fill=(75, 85, 99), width=1)
    draw.text((40, 510), "Verified Boxing Training Facility", fill=(107, 114, 128))
    draw.text((40, 535), "Addis Ababa, Ethiopia", fill=(107, 114, 128))

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return ContentFile(buf.getvalue(), name=f"receipt_{tx_id.lower()}.jpg")


class Command(BaseCommand):
    help = "Seeds Tade Boxing Club with settings, Coach Tade account, and 30 realistic members."

    def handle(self, *args, **options):
        self.stdout.write("Seeding Tade Boxing Club database...")

        # 1. Club Settings
        settings = ClubSettings.get_settings()
        settings.club_name = "Tade Boxing Club"
        settings.monthly_subscription_amount = Decimal('1500.00')
        settings.currency_symbol = "ETB"
        settings.payment_deadline_day = 5
        settings.contact_phone = "+251 91 123 4567"
        settings.contact_email = "coach@tadebox.com"
        settings.location = "Bole Subcity, Addis Ababa, Ethiopia"
        settings.payment_instructions = (
            "1. Commercial Bank of Ethiopia (CBE):\n"
            "   Account Name: Tade Boxing Club\n"
            "   Account Number: 1000234567890\n\n"
            "2. Telebirr:\n"
            "   Mobile / Merchant Number: 0911223344 (Coach Tade)\n\n"
            "3. Awash Bank:\n"
            "   Account Number: 01425678912300\n\n"
            "Important: Transfer exactly 1,500 ETB before the 5th of each month and upload your transfer confirmation screenshot."
        )
        settings.save()
        self.stdout.write(self.style.SUCCESS("[OK] Club settings initialized."))

        # 2. Coach Superuser Account
        coach_user, created = User.objects.get_or_create(username='coach_tade')
        coach_user.first_name = "Tade"
        coach_user.last_name = "Bekele"
        coach_user.email = "coach@tadebox.com"
        coach_user.is_staff = True
        coach_user.is_superuser = True
        coach_user.set_password('boxing2026')
        coach_user.save()
        if created:
            self.stdout.write(self.style.SUCCESS("[OK] Created Coach user: coach_tade (password: boxing2026)"))
        else:
            self.stdout.write(self.style.SUCCESS("[OK] Coach user updated: coach_tade (password: boxing2026)"))

        # 3. 30 Realistic Ethiopian Boxer Members
        members_data = [
            ("abebe_b", "Abebe Bikila", "0911100001", "Featherweight champion, Mon/Wed/Fri"),
            ("haile_g", "Haile Gebrselassie", "0911100002", "Lightweight, sparring focus"),
            ("kenenisa_b", "Kenenisa Bekele", "0911100003", "Welterweight, morning sessions"),
            ("derartu_t", "Derartu Tulu", "0911100004", "Flyweight contender"),
            ("sileshi_s", "Sileshi Sihine", "0911100005", "Middleweight, strong southpaw"),
            ("tirunesh_d", "Tirunesh Dibaba", "0911100006", "Bantamweight, speed training"),
            ("meseret_d", "Meseret Defar", "0911100007", "Featherweight, technical counterpuncher"),
            ("tariku_b", "Tariku Bekele", "0911100008", "Light Heavyweight"),
            ("girma_w", "Girma Wolde-Chirkos", "0911100009", "Welterweight"),
            ("almaz_a", "Almaz Ayana", "0911100010", "Super Featherweight"),
            ("yifter_m", "Miruts Yifter", "0911100011", "Gear shifter, powerful hooks"),
            ("gezahegne_a", "Gezahegne Abera", "0911100012", "Cruiserweight, defensive stylist"),
            ("million_w", "Million Wolde", "0911100013", "Lightweight"),
            ("senbere_t", "Senbere Teferi", "0911100014", "Light flyweight"),
            ("selemon_b", "Selemon Barega", "0911100015", "Super Welterweight, heavy hitter"),
            ("berhane_a", "Berhane Adere", "0911100016", "Flyweight"),
            ("lemi_b", "Lemi Berhanu", "0911100017", "Middleweight"),
            ("feyisa_l", "Feyisa Lilesa", "0911100018", "Welterweight, cross punch specialist"),
            ("letesenbet_g", "Letesenbet Gidey", "0911100019", "Lightweight world class competitor"),
            ("yomif_k", "Yomif Kejelcha", "0911100020", "Heavyweight, power reach"),
            ("tadese_w", "Tadese Worku", "0911100021", "Lightweight, beginner prospect"),
            ("dejen_g", "Dejen Gebremeskel", "0911100022", "Super Lightweight"),
            ("genzebe_d", "Genzebe Dibaba", "0911100023", "Bantamweight"),
            ("solomon_d", "Solomon Deksisa", "0911100024", "Super Middleweight"),
            ("birhanu_l", "Birhanu Legese", "0911100025", "Cruiserweight"),
            ("gudaf_t", "Gudaf Tsegay", "0911100026", "Featherweight knockout power"),
            ("mosinet_g", "Mosinet Geremew", "0911100027", "Super Welterweight"),
            ("ababel_y", "Ababel Yeshaneh", "0911100028", "Super Bantamweight"),
            ("sisay_l", "Sisay Lemma", "0911100029", "Heavyweight sparring partner"),
            ("tamirat_t", "Tamirat Tola", "0911100030", "Light Heavyweight title aspirant"),
        ]

        # Additional 2 inactive members to test inactive exclusion
        inactive_members_data = [
            ("dawit_z", "Dawit Zerihun", "0911100098", "Injured wrist, temporarily inactive"),
            ("mulu_a", "Mulu Alemayehu", "0911100099", "Relocated outside Addis Ababa"),
        ]

        created_member_profiles = []

        for username, display_name, phone, notes in members_data:
            user, u_created = User.objects.get_or_create(username=username)
            if u_created:
                user.set_password('boxer2026')
                user.email = f"{username}@tadebox.local"
                user.save()

            profile, _ = MemberProfile.objects.get_or_create(
                user=user,
                defaults={
                    'display_name': display_name,
                    'phone_number': phone,
                    'notes': notes,
                    'is_active_member': True,
                    'join_date': date(2026, 1, 15)
                }
            )
            created_member_profiles.append(profile)

        for username, display_name, phone, notes in inactive_members_data:
            user, _ = User.objects.get_or_create(username=username)
            user.set_password('boxer2026')
            user.save()
            MemberProfile.objects.get_or_create(
                user=user,
                defaults={
                    'display_name': display_name,
                    'phone_number': phone,
                    'notes': notes,
                    'is_active_member': False,
                    'join_date': date(2025, 10, 1)
                }
            )

        self.stdout.write(self.style.SUCCESS(f"[OK] Initialized {len(created_member_profiles)} active members (password: boxer2026) and 2 inactive members."))

        # 4. Initialize Previous Month (September 2026) records to prove persistence
        sep_month = date(2026, 9, 1)
        for i, member in enumerate(created_member_profiles):
            sub, _ = Subscription.objects.get_or_create(
                member=member,
                subscription_month=sep_month,
                defaults={
                    'amount_due': Decimal('1500.00'),
                    'status': Subscription.Status.PAID if i < 26 else Subscription.Status.UNPAID,
                    'paid_at': timezone.now() - timedelta(days=35) if i < 26 else None,
                    'verified_by': coach_user if i < 26 else None
                }
            )

        self.stdout.write(self.style.SUCCESS("[OK] September 2026 historical records populated."))

        # 5. Initialize Current Month (October 2026) records with realistic status distribution
        current_month = get_current_subscription_month()
        # 14 Paid, 6 Pending with sample receipts, 10 Unpaid
        for i, member in enumerate(created_member_profiles):
            sub, _ = Subscription.objects.get_or_create(
                member=member,
                subscription_month=current_month,
                defaults={'amount_due': Decimal('1500.00'), 'status': Subscription.Status.UNPAID}
            )

            if i < 14:
                # Paid members
                sub.status = Subscription.Status.PAID
                sub.paid_at = timezone.now() - timedelta(days=2)
                sub.verified_by = coach_user
                sub.save()

                if not sub.submissions.exists():
                    receipt_file = generate_sample_receipt(member.display_name, "1,500.00", f"CBE-TX-{1000 + i}")
                    PaymentSubmission.objects.create(
                        subscription=sub,
                        member=member,
                        screenshot=receipt_file,
                        transaction_reference=f"CBE-TX-{1000 + i}",
                        status=PaymentSubmission.Status.APPROVED,
                        reviewed_at=timezone.now() - timedelta(days=2),
                        reviewed_by=coach_user,
                        admin_notes="Verified against CBE statement."
                    )
            elif 14 <= i < 20:
                # Pending members awaiting coach verification
                sub.status = Subscription.Status.PENDING
                sub.save()

                if not sub.submissions.exists():
                    receipt_file = generate_sample_receipt(member.display_name, "1,500.00", f"TB-TX-{2000 + i}")
                    PaymentSubmission.objects.create(
                        subscription=sub,
                        member=member,
                        screenshot=receipt_file,
                        transaction_reference=f"TB-TX-{2000 + i}",
                        status=PaymentSubmission.Status.PENDING
                    )
            else:
                # Unpaid members
                sub.status = Subscription.Status.UNPAID
                sub.paid_at = None
                sub.verified_by = None
                sub.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"[OK] Current month ({current_month.strftime('%B %Y')}) seeded: "
                "14 Paid, 6 Pending Verification, 10 Unpaid."
            )
        )
