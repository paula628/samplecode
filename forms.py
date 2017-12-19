from __future__ import unicode_literals

from datetime import datetime

from django import forms
from django.forms import inlineformset_factory
from django.forms import modelformset_factory
from django.utils.translation import ugettext_lazy as _
from django.db.models import Q

from .models import Invoice
from .models import InvoiceItem
from .models import InvoicePayment

from cost_centers.models import CostCenter


MY_DATE_FORMATS = ['%Y-%m-%d', ]


class InvoiceForm(forms.ModelForm):

    class Meta:
        model = Invoice
        fields = [
            'company', 'cost_center', 'invoice_number',
            'address', 'client_contact', 'period',
            'start_date', 'end_date', 'date', 'due_date',
            'client_display', 'business_style', 'purchase_order',
            'total_manhours']

        widgets = {
            'company': forms.HiddenInput(),
            'client_display': forms.TextInput(
                attrs={'placeholder': 'Client name to display (optional)'})
        }

        labels = {
            'total_manhours': _('Total Manhours Billed'),
            }

    def __init__(self, *args, **kwargs):
        self.obj = kwargs.get('instance')
        super(InvoiceForm, self).__init__(*args, **kwargs)
        self.fields['cost_center'].required = True
        choices, centers = [], []
        if self.obj:
            detachments = self.obj.company.detachment_set.filter(is_active=True)
            centers = CostCenter.objects.filter(Q(company=self.obj.company) |
                                                Q(detachment__in=detachments), is_active=True)
        else:
            initial = kwargs.get('initial', None)
            company = initial.get('company', None) if initial else None
            if company:
                detachments = company.detachment_set.filter(is_active=True)
                centers = CostCenter.objects.filter(Q(company=company) |
                                                Q(detachment__in=detachments), is_active=True)
        if centers:
            for i in centers:
                choices.append((i.pk, i.name))
        self.fields['cost_center'].choices = choices

        for k, field in self.fields.items():
            if 'required' in field.error_messages:
                msg = '{} is required.'.format(field.label)
                field.error_messages['required'] = msg
            if 'invalid' in field.error_messages:
                msg = "Enter a valid {}".format(field.label)
                field.error_messages['invalid'] = msg


    def clean_invoice_number(self):
        invoice_num = self.cleaned_data.get('invoice_number', None)
        invoice = False
        if invoice_num:
            try:
                int(invoice_num)
            except ValueError:
                msg = "The invoice number should only contain numbers."
                self.add_error('invoice_number', msg)

            invoice = Invoice.objects.active().filter(invoice_number=invoice_num)
            if self.obj:
                invoice = invoice.exclude(pk=self.obj.pk)
                
            if invoice:
                msg = "An invoice with invoice number {} already exists!"
                self.add_error('invoice_number', msg.format(invoice_num))
        return invoice_num

class InvoiceItemForm(forms.ModelForm):
    hours = forms.DecimalField(required=False, decimal_places=2)
    rate = forms.DecimalField(required=False, decimal_places=2)
    amount = forms.DecimalField(required=False, decimal_places=2)

    def __init__(self, *args, **kwargs):
        super(InvoiceItemForm, self).__init__(*args, **kwargs)
        self.empty_permitted = True
        self.fields['description'].required = False
        self.fields['type'].required = False

    class Meta:
        model = InvoiceItem
        fields = ['type', 'description', 'equivalent_guard_shift', 'hours',
                  'rate', 'amount']

    def clean(self):
        cleaned_data = super(InvoiceItemForm, self).clean()
        amount = cleaned_data.get('amount', None)
        item_type = cleaned_data.get('type', None)

        if item_type:
            if item_type != 'text' and amount is None:
                msg = "Amount is required if type is not Text."
                self.add_error('amount', msg)
        else:
            if amount:
                msg = "Type is required"
                self.add_error('type', msg)
        return cleaned_data


target = ('type', 'description', 'equivalent_guard_shift', 'hours',
          'rate', 'amount')

DuplicateInvoiceItemFormset = modelformset_factory(
    InvoiceItem, form=InvoiceItemForm, fields=target)

InvoiceItemFormset = inlineformset_factory(
    Invoice, InvoiceItem, form=InvoiceItemForm, min_num=1, validate_min=True,
    extra=0, can_delete=True)


class InvoicePaymentForm(forms.ModelForm):
    date = forms.CharField(widget=forms.TextInput(
        attrs={'placeholder': 'YYYY/MM/DD e.g. 2016/12/25'}))

    class Meta:
        model = InvoicePayment
        fields = ['date', 'amount']

    def clean(self):
        cleaned_data = super(InvoicePaymentForm, self).clean()

        try:
            cleaned_data['date'] = datetime.strptime(
                cleaned_data['date'], '%Y/%m/%d').date()
        except:
            raise forms.ValidationError('Invalid date format.')

        amount = cleaned_data['amount']
        if not amount:
            raise forms.ValidationError('Payment amount cannot be zero.')

        return cleaned_data
