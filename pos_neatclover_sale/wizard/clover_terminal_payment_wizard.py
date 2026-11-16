# -*- coding: utf-8 -*-
from decimal import Decimal

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CloverTerminalPaymentWizard(models.TransientModel):
    _name = 'clover.terminal.payment.wizard'
    _description = 'Clover Terminal Payment Wizard'

    sale_order_id = fields.Many2one('sale.order', readonly=True)
    invoice_id = fields.Many2one('account.move', readonly=True)
    payment_method_id = fields.Many2one(
        'pos.payment.method',
        string='Terminal',
        required=True,
        domain=[('use_payment_terminal', '=', 'neatclover')],
    )
    payment_method_locked = fields.Boolean(default=False, readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    amount_total = fields.Monetary(string='Total Due', currency_field='currency_id', readonly=True)
    amount_preauth_available = fields.Monetary(
        string='Available to Pre-Authorize',
        currency_field='currency_id',
        readonly=True,
    )
    amount = fields.Monetary(string='Amount', currency_field='currency_id', required=True)
    is_preauth = fields.Boolean(string='Pre-Authorize', default=False)
    preauth_label = fields.Char(string='Pre-Auth Reference')
    partner_id = fields.Many2one('res.partner', readonly=True)
    order_id = fields.Char(readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('done', 'Done'),
    ], default='draft', readonly=True)
    operation = fields.Selection([
        ('payment', 'Pay'),
        ('preauth', 'Pre-Authorize'),
    ], readonly=True)
    transaction_id = fields.Char(readonly=True)
    payment_page_html = fields.Html(string='Payment', sanitize=False, compute='_compute_payment_page_html')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        helpers = self.env['clover.document.payment']
        sale_order = self.env['sale.order'].browse(self.env.context.get('default_sale_order_id')).exists()
        invoice = self.env['account.move'].browse(self.env.context.get('default_invoice_id')).exists()

        if sale_order:
            helpers.check_sale_order(sale_order)
            order_id = helpers.order_id_for_sale_order(sale_order)
            remaining = helpers.get_remaining_due_amount(sale_order=sale_order)
            available_preauth = helpers.get_preauth_available_amount(
                sale_order=sale_order,
            )
            label = self.env['neat.clover.payment.request'].suggest_preauth_label(
                sale_order.name or '',
            )
            vals.update({
                'sale_order_id': sale_order.id,
                'order_id': order_id,
                'partner_id': sale_order.partner_id.id,
                'currency_id': sale_order.currency_id.id,
                'amount_total': remaining,
                'amount_preauth_available': available_preauth,
                'amount': remaining,
                'preauth_label': label,
            })
            currency = sale_order.currency_id
            if currency.compare_amounts(remaining, 0) > 0:
                locked_method = helpers.get_locked_payment_method_for_order(order_id)
                if locked_method:
                    vals.update({
                        'payment_method_id': locked_method.id,
                        'payment_method_locked': True,
                    })
        elif invoice:
            helpers.check_invoice(invoice)
            order_id = helpers.order_id_for_invoice(invoice)
            remaining = helpers.get_remaining_due_amount(invoice=invoice)
            available_preauth = helpers.get_preauth_available_amount(
                invoice=invoice,
            )
            label = self.env['neat.clover.payment.request'].suggest_preauth_label(
                invoice.name or invoice.ref or '',
            )
            vals.update({
                'invoice_id': invoice.id,
                'order_id': order_id,
                'partner_id': invoice.partner_id.id,
                'currency_id': invoice.currency_id.id,
                'amount_total': remaining,
                'amount_preauth_available': available_preauth,
                'amount': remaining,
                'preauth_label': label,
            })
            currency = invoice.currency_id
            if currency.compare_amounts(remaining, 0) > 0:
                locked_method = helpers.get_locked_payment_method_for_order(order_id)
                if locked_method:
                    vals.update({
                        'payment_method_id': locked_method.id,
                        'payment_method_locked': True,
                    })
        else:
            raise ValidationError(_('Please open this wizard from a sales order or invoice.'))
        return vals

    @api.depends('state', 'transaction_id', 'operation')
    def _compute_payment_page_html(self):
        for wizard in self:
            wizard.payment_page_html = False
            if wizard.state == 'processing' and wizard.transaction_id:
                page_type = 'preauth' if wizard.operation == 'preauth' else 'payment'
                page_url = '/pos_clover/terminal_payment_page/%s/%s' % (page_type, wizard.id)
                wizard.payment_page_html = (
                    '<div class="clover-terminal-iframe-wrap">'
                    '<iframe class="clover-terminal-iframe" src="%s" '
                    'allow="payment *"></iframe></div>' % page_url
                )

    def _validate_terminal(self):
        self.ensure_one()
        payment_method = self.payment_method_id
        if not payment_method.neat_clover_terminal_device_code:
            raise ValidationError(_('The selected terminal has no device code configured.'))
        if self.payment_method_locked:
            locked_method = self.env['clover.document.payment'].get_locked_payment_method_for_order(self.order_id)
            if locked_method and locked_method != payment_method:
                raise ValidationError(_(
                    'Further terminal operations on this document must use the same terminal as the first payment.'
                ))
        return payment_method

    def action_confirm(self):
        """Single entry point: Pay or Pre-Authorize based on the checkbox."""
        self.ensure_one()
        if self.is_preauth:
            return self.action_start_preauth()
        return self.action_start_payment()

    def action_start_payment(self):
        self.ensure_one()
        if self.currency_id.compare_amounts(self.amount, 0) <= 0:
            raise ValidationError(_('Payment amount must be greater than zero.'))
        if self.currency_id.compare_amounts(self.amount, self.amount_total) > 0:
            raise ValidationError(_('Payment amount cannot exceed the remaining due amount.'))

        payment_method = self._validate_terminal()

        from odoo.addons.pos_neatclover.controllers.main import PosCloverController
        result = PosCloverController().create_payment_request(
            terminal_id=payment_method.neat_clover_terminal_device_code,
            order_id=self.order_id,
            amount=self.amount,
            user_id=self.env.user.id,
            is_document_payment=True,
        )
        if not result or result.get('status') not in (200, 201):
            raise ValidationError(_('Could not create the terminal payment request.'))

        self.write({
            'state': 'processing',
            'operation': 'payment',
            'transaction_id': result['data']['transaction_id'],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Pay by Clover Terminal'),
            'res_model': 'clover.terminal.payment.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': dict(self.env.context, dialog_size='medium'),
        }

    def action_start_preauth(self):
        self.ensure_one()
        label = (self.preauth_label or '').strip()
        if not label:
            raise ValidationError(_('Pre-Auth Reference is required.'))
        if self.env['neat.clover.payment.request'].is_preauth_label_taken(label):
            raise ValidationError(_('Pre-Auth Reference must be unique among active pre-authorizations.'))
        if self.currency_id.compare_amounts(self.amount, 0) <= 0:
            raise ValidationError(_('Pre-authorization amount must be greater than zero.'))
        # Pre-auth may exceed the document remaining due; charge/pay stay capped elsewhere.

        payment_method = self._validate_terminal()
        partner = self.partner_id
        if not partner:
            raise ValidationError(_('A customer is required for pre-authorization.'))

        from odoo.addons.pos_neatclover.controllers.main import PosCloverController
        result = PosCloverController().create_payment_request(
            terminal_id=payment_method.neat_clover_terminal_device_code,
            order_id=self.order_id,
            amount=self.amount,
            user_id=self.env.user.id,
            is_document_payment=True,
            request_type='preauth',
            preauth_label=label,
            partner_id=partner.id,
        )
        if not result or result.get('status') not in (200, 201):
            if result and result.get('error') == 'preauth_label_taken':
                raise ValidationError(_('Pre-Auth Reference must be unique among active pre-authorizations.'))
            raise ValidationError(_('Could not create the terminal pre-authorization request.'))

        self.write({
            'state': 'processing',
            'operation': 'preauth',
            'transaction_id': result['data']['transaction_id'],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Pre-Authorize by Clover Terminal'),
            'res_model': 'clover.terminal.payment.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': dict(self.env.context, dialog_size='medium'),
        }

    def action_mark_done(self):
        self.write({'state': 'done'})
        return {'type': 'ir.actions.act_window_close'}
