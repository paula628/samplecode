from __future__ import unicode_literals

import datetime

from decimal import Decimal

from reportlab.lib.units import cm
from reportlab.platypus import Spacer
from reportlab.platypus import TableStyle
from reportlab.platypus.tables import Table

from django.db import models
from django.db.models import Sum

from core.models import TimeStampedModel
from cost_centers.models import CostCenter
from client.models import Company
from payroll_core.models import PeriodCode
from payroll_rates.models import PayrollRate


INVOICE_ITEM_TYPE = (
    ('allowances', 'Allowances (+)'),
    ('sales', 'Sales (+)'),
    ('text', 'Text'),
    ('vat', 'VAT (+)'),
    ('tax', 'Withholding Tax (-)'),
    ('adjustments_add', 'Adjustments (+)'),
    ('adjustments_deduct', 'Adjustments (-)'),
)

INVOICE_STATUS = (
    ('all', 'All'),
    ('paid', 'Paid'),
    ('overdue', 'Overdue'),
    ('sent', 'Sent'),
)


class InvoiceQuerySet(models.QuerySet):

    def paid(self):
        return self.filter(fully_paid=True)

    def overdue(self):
        today = datetime.date.today()
        return self.filter(fully_paid=False, due_date__lt=today)

    def sent(self):
        today = datetime.date.today()
        return self.filter(fully_paid=False, due_date__gt=today)

    def active(self):
        return self.filter(is_active=True).extra(
            select={'number': 'CAST(invoice_number AS INTEGER)'}
        ).order_by('-period__period_code')


class InvoiceManager(models.Manager):

    def get_queryset(self):
        return InvoiceQuerySet(self.model, using=self._db).select_related()

    def paid(self):
        return self.get_queryset().paid()

    def overdue(self):
        return self.get_queryset().overdue()

    def sent(self):
        return self.get_queryset().sent()

    def active(self):
        return self.get_queryset().active()


class Invoice(TimeStampedModel):
    client_display = models.CharField(max_length=128, blank=True, null=True)
    company = models.ForeignKey(Company)
    address = models.CharField(max_length=256)

    pay_levels = models.ManyToManyField(PayrollRate, null=True, blank=True)
    cost_center = models.ForeignKey(CostCenter, null=True, blank=True)

    client_contact = models.CharField(max_length=128)
    period = models.ForeignKey(PeriodCode)
    date = models.DateField()
    total_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=Decimal('0.00')
    )
    due_date = models.DateField()
    fully_paid = models.BooleanField(default=False)
    date_fully_paid = models.DateField(blank=True, null=True)
    invoice_number = models.CharField(max_length=24)
    start_date = models.DateField()
    end_date = models.DateField()
    business_style = models.CharField(max_length=128, blank=True, null=True)
    purchase_order = models.CharField(max_length=24, blank=True, null=True)
    cancelled = models.BooleanField(default=False)
    total_manhours = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=Decimal('0.00')
    )
    objects = InvoiceManager()

    class Meta:
        ordering = ['-pk']

    def __unicode__(self):
        return '{} - {}'.format(self.company, self.period)

    def get_total_paid(self):
        payments = self.invoicepayment_set.filter(
            is_active=True).select_related().order_by('-date')
        total_paid = sum([pay.amount for pay in payments])
        return payments, total_paid

    @property
    def get_client_name(self):
        return self.client_display \
            if self.client_display else self.company.get_name()

    def get_status(self):
        """
        Statuses: SENT, OVERDUE, PAID.
        if it's not overdue but unpaid or partially paid, status is SENT.
        if it's overdue and unpaid or partially paid, then status is OVERDUE.
        """
        res = {'status': '', 'remark': ''}
        today = datetime.date.today()
        if self.cancelled:
            res['status'] = 'Cancelled'
        elif self.due_date >= today and not self.fully_paid:
            res['status'] = 'Sent'
            days_due = self.due_date - today
            res['remark'] = days_due.days
        elif self.due_date < today and not self.fully_paid:
            res['status'] = 'Overdue'
            days_late = today - self.due_date
            res['remark'] = days_late.days
        elif self.fully_paid and self.date_fully_paid > self.due_date:
            res['status'] = 'Paid'
            days_late = self.date_fully_paid - self.due_date
            res['remark'] = days_late.days
        elif self.fully_paid and self.date_fully_paid <= self.due_date:
            res['status'] = 'Paid'
        return res

    def update_fully_paid(self):
        payments, total_paid = self.get_total_paid()
        total_amount = self.total_amount
        if total_paid >= Decimal(total_amount):
            self.fully_paid = True
            self.date_fully_paid = payments.latest('date').date
        else:
            self.fully_paid = False
            self.date_fully_paid = None
        self.save()
        return True

    def print_invoice_data(self):
        elements = []
        invoice_data = []
        if self.purchase_order:
            invoice_data.append(('', '', self.purchase_order))
        else:
            invoice_data.append(('', '', ''))
        issue_date = self.date.strftime('%B %d, %Y')
        invoice_data.append(('', self.get_client_name, issue_date))
        start_date = self.start_date.strftime('%m/%d/%Y')
        end_date = self.end_date.strftime('%m/%d/%Y')
        dates = start_date + ' - ' + end_date
        invoice_data.append(('', self.address, dates))
        invoice_data.append(('', self.business_style, ''))
        invoice_data.append(('', self.client_contact, ''))
        invoice_table = Table(
            invoice_data,
            colWidths=[1.8*cm, 12.8*cm, 4.4*cm],
            rowHeights=17
        )
        fontsize = ('FONTSIZE', (0, 0), (2, len(invoice_data)-1), 11)
        fontstyle = ('FONT', (0, 0), (2, len(invoice_data)-1), 'Times-Bold')
        po_padding = ('BOTTOMPADDING', (0, 0), (2, 0), 17)
        styles = [fontsize, fontstyle, po_padding]
        if len(self.address) > 70:
            styles.append(('FONTSIZE', (0, 2), (1, 2), 9.5))
        invoice_table.setStyle(TableStyle(styles))

        item_data = []
        row = 0

        for i in self.invoiceitem_set.filter(is_active=True).order_by('pk'):
            amount = '{:,}'.format(i.amount) if i.amount else None
            if i.type == 'text':
                item_data.append(('', i.description, '', '', '', amount))
            else:
                rate = '{:,}'.format(i.rate) if i.rate else None
                shift = i.equivalent_guard_shift \
                    if i.equivalent_guard_shift else None
                hours = '{:,} hrs'.format(i.hours) if i.hours else None
                item_data.append((
                    '', i.description, shift, hours, rate, amount))
            row += 1
        item_table = Table(
            item_data,
            colWidths=[1.8*cm, 7.25*cm, 2.3*cm, 2.45*cm, 2.35*cm, 2.3*cm],
            rowHeights=18
        )
        fontsize_items = ('FONTSIZE', (0, 0), (5, len(item_data)-1), 11)
        fontstyle_items = (
            'FONT', (0, 0), (5, len(item_data)-1), 'Times-Roman'
        )
        align_amount = ('ALIGN', (5, 0), (5, len(item_data)-1), "RIGHT")
        styles = [fontsize_items, fontstyle_items, align_amount]
        item_table.setStyle(TableStyle(styles))
        elements.append(invoice_table)
        elements.append(Spacer(0, 7.6*cm))
        elements.append(item_table)
        return elements

    def get_sales(self):
        sales = 0
        tax = 0
        items = self.invoiceitem_set.filter(
            type__in=['sales', 'tax'], is_active=True
        ).values(
            'type'
        ).annotate(
            Sum('amount')
        ).values(
            'type', 'amount__sum'
        ).order_by(
            'type'
        ).distinct()
        for i in items.iterator():
            if 'type' in i and i['type'] == 'sales':
                sales = i['amount__sum']
            elif 'type' in i and i['type'] == 'tax':
                tax = i['amount__sum']
        return sales - tax


class InvoiceItem(TimeStampedModel):
    description = models.CharField(max_length=256, blank=True, null=True)
    equivalent_guard_shift = models.CharField(
        max_length=24,
        blank=True,
        null=True
    )
    hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True
    )
    rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True
    )
    invoice = models.ForeignKey(Invoice)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True
    )
    type = models.CharField(max_length=24, choices=INVOICE_ITEM_TYPE)

    class Meta:
        ordering = ['-pk']

    def __unicode__(self):
        return '{} - {}'.format(self.type, self.invoice)


class InvoicePayment(TimeStampedModel):
    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 default=Decimal('0.00'))
    invoice = models.ForeignKey(Invoice)

    class Meta:
        ordering = ['-pk']

    def __unicode__(self):
        return '{} - {}'.format(self.invoice, self.amount)
