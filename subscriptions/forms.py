import os
from decimal import Decimal
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.conf import settings
from .models import PaymentSubmission, MemberProfile, ClubSettings, Subscription

User = get_user_model()


class PaymentSubmissionForm(forms.ModelForm):
    class Meta:
        model = PaymentSubmission
        fields = ['screenshot', 'transaction_reference']
        widgets = {
            'screenshot': forms.FileInput(attrs={
                'class': 'block w-full text-sm text-gray-300 file:mr-4 file:py-2.5 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-red-600 file:text-white hover:file:bg-red-700 cursor-pointer bg-slate-800 border border-slate-700 rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-red-500',
                'accept': 'image/jpeg,image/png,image/webp,image/jpg',
                'id': 'payment-screenshot-input'
            }),
            'transaction_reference': forms.TextInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-red-500',
                'placeholder': 'e.g. CBE-TX-984729 or Telebirr Ref (optional)',
                'id': 'payment-tx-ref-input'
            }),
        }

    def clean_screenshot(self):
        screenshot = self.cleaned_data.get('screenshot')
        if not screenshot:
            raise ValidationError("Please upload a payment screenshot.")

        # Validate file size (max 5 MB)
        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
        if screenshot.size > max_size:
            max_mb = max_size / (1024 * 1024)
            raise ValidationError(f"Image file size cannot exceed {max_mb:.0f} MB.")

        # Validate file extension
        ext = os.path.splitext(screenshot.name)[1].lower()
        allowed_extensions = getattr(settings, 'ALLOWED_UPLOAD_EXTENSIONS', ['.jpg', '.jpeg', '.png', '.webp'])
        if ext not in allowed_extensions:
            raise ValidationError(
                f"Unsupported image format ({ext}). Allowed formats: {', '.join(allowed_extensions)}"
            )

        return screenshot


class PaymentReviewForm(forms.Form):
    ACTION_CHOICES = [
        ('APPROVE', 'Approve Payment'),
        ('REJECT', 'Reject Submission'),
    ]
    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'text-red-600 focus:ring-red-500'})
    )
    rejection_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-3 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'Please provide clear feedback to the member (required if rejecting)...'
        })
    )
    admin_notes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'Internal note (optional)'
        })
    )

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get('action')
        rejection_reason = cleaned_data.get('rejection_reason', '').strip()

        if action == 'REJECT' and not rejection_reason:
            self.add_error('rejection_reason', 'A reason is required when rejecting a payment submission.')

        return cleaned_data


class ManualStatusUpdateForm(forms.Form):
    status = forms.ChoiceField(
        choices=Subscription.Status.choices,
        widget=forms.Select(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
        })
    )
    reason = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-3 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'State the audit reason for manually changing this member status...'
        }),
        help_text="Required for club audit records."
    )


class MemberCreateForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'e.g. abeberad'
        })
    )
    display_name = forms.CharField(
        max_length=120,
        widget=forms.TextInput(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'Full Name (e.g. Abebe Bikila)'
        })
    )
    phone_number = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'e.g. 0911223344'
        })
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'member@example.com'
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'Temporary login password'
        })
    )
    emergency_contact = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'Contact Name & Phone'
        })
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
            'placeholder': 'Weight category, training days, or membership notes...'
        })
    )

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username=username).exists():
            raise ValidationError("This username is already taken. Please choose another.")
        return username


class MemberEditForm(forms.ModelForm):
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
        })
    )

    class Meta:
        model = MemberProfile
        fields = ['display_name', 'phone_number', 'is_active_member', 'emergency_contact', 'notes']
        widgets = {
            'display_name': forms.TextInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
            'phone_number': forms.TextInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
            'is_active_member': forms.CheckboxInput(attrs={
                'class': 'w-4 h-4 text-red-600 bg-slate-800 border-slate-700 rounded focus:ring-red-500'
            }),
            'emergency_contact': forms.TextInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
            'notes': forms.Textarea(attrs={
                'rows': 3,
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields['email'].initial = self.instance.user.email

    def save(self, commit=True):
        member = super().save(commit=commit)
        email = self.cleaned_data.get('email')
        if member.user and member.user.email != email:
            member.user.email = email
            member.user.save(update_fields=['email'])
        return member


class ClubSettingsForm(forms.ModelForm):
    class Meta:
        model = ClubSettings
        fields = [
            'club_name',
            'monthly_subscription_amount',
            'currency_symbol',
            'payment_deadline_day',
            'payment_instructions',
            'contact_phone',
            'contact_email',
            'location'
        ]
        widgets = {
            'club_name': forms.TextInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
            'monthly_subscription_amount': forms.NumberInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
                'step': '1.00'
            }),
            'currency_symbol': forms.TextInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
            'payment_deadline_day': forms.NumberInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500',
                'min': 1,
                'max': 28
            }),
            'payment_instructions': forms.Textarea(attrs={
                'rows': 6,
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-3 text-white font-mono text-sm focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
            'contact_phone': forms.TextInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
            'contact_email': forms.EmailInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
            'location': forms.TextInput(attrs={
                'class': 'w-full bg-slate-800 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:ring-2 focus:ring-red-500'
            }),
        }
