# -*- coding: utf-8 -*-
from decimal import Decimal

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CloverTerminalChargeWizard(models.TransientModel):
    _name = 'clover.terminal.charge.wizard'
    _description = 'Clover Terminal Charge / Void Pre-Authorization Wizard'

    sale_order_id = fields.Many2one('sale.order', readonly=True)
    invoice_id = fields.Many2one('account.move', readonly=True)
    preauth_request_id = fields.Many2one(
        'neat.clover.payment.request',
        string='Pre-Authorization',
        required=True,
    )
    payment_method_id = fields.Many2one(
        'pos.payment.method',
        string='Terminal',
        required=True,
        readonly=True,
    )
    payment_method_locked = fields.Boolean(default=True, readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    amount_due = fields.Monetary(string='Remaining Due', currency_field='currency_id', readonly=True)
    amount_authorized = fields.Monetary(string='Original Authorization', currency_field='currency_id', readonly=True)
    amount_max = fields.Monetary(string='Remaining Chargeable', currency_field='currency_id', readonly=True)
    amount = fields.Monetary(string='Charge Amount', currency_field='currency_id', required=True)
    order_id = fields.Char(readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('done', 'Done'),
    ], default='draft', readonly=True)
    transaction_id = fields.Char(readonly=True)
    operation = fields.Selection([
        ('capture', 'Charge'),
        ('void', 'Void'),
    ], readonly=True)
    payment_page_html = fields.Html(string='Terminal', sanitize=False, compute='_compute_payment_page_html')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        helpers = self.env['clover.document.payment']
        sale_order = self.env['sale.order'].browse(self.env.context.get('default_sale_order_id')).exists()
        invoice = self.env['account.move'].browse(self.env.context.get('default_invoice_id')).exists()

        if sale_order:
            helpers.check_sale_order(sale_order)
            order_id = helpers.order_id_for_sale_order(sale_order)
            partner = sale_order.partner_id
            currency = sale_order.currency_id
            remaining = helpers.get_remaining_due_amount(sale_order=sale_order)
            vals.update({
                'sale_order_id': sale_order.id,
                'order_id': order_id,
                'partner_id': partner.id,
                'currency_id': currency.id,
                'amount_due': remaining,
            })
        elif invoice:
            helpers.check_invoice(invoice)
            order_id = helpers.order_id_for_invoice(invoice)
            partner = invoice.partner_id
            currency = invoice.currency_id
            remaining = helpers.get_remaining_due_amount(invoice=invoice)
            vals.update({
                'invoice_id': invoice.id,
                'order_id': order_id,
                'partner_id': partner.id,
                'currency_id': currency.id,
                'amount_due': remaining,
            })
        else:
            raise ValidationError(_('Please open this wizard from a sales order or invoice.'))

        open_preauths = helpers.get_open_preauth_requests(partner_id=partner.id)
        if not open_preauths:
            raise ValidationError(_('There are no open pre-authorizations for this customer.'))

        preauth = open_preauths[0]
        payment_method = helpers._payment_method_for_terminal(preauth.terminal_id)
        if not payment_method:
            raise ValidationError(_('The terminal used for the selected pre-authorization is no longer configured.'))

        auth_amount = float(Decimal(preauth.amount) / Decimal('100'))
        remaining_auth = float(Decimal(helpers.get_preauth_remaining_cents(preauth)) / Decimal('100'))
        max_charge = min(remaining_auth, remaining) if currency.compare_amounts(remaining, 0) > 0 else 0.0
        vals.update({
            'preauth_request_id': preauth.id,
            'payment_method_id': payment_method.id,
            'payment_method_locked': True,
            'amount_authorized': auth_amount,
            'amount_max': max_charge,
            'amount': max_charge if currency.compare_amounts(max_charge, 0) > 0 else 0.0,
        })
        return vals

    @api.onchange('preauth_request_id')
    def _onchange_preauth_request_id(self):
        helpers = self.env['clover.document.payment']
        for wizard in self:
            preauth = wizard.preauth_request_id
            if not preauth:
                continue
            if preauth.partner_id and wizard.partner_id and preauth.partner_id != wizard.partner_id:
                raise ValidationError(_('Please select an open pre-authorization for this customer.'))
            if preauth.status not in helpers._OPEN_PREAUTH_STATUSES:
                raise ValidationError(_('Please select an open pre-authorization for this customer.'))
            if helpers.get_preauth_remaining_cents(preauth) <= 0:
                raise ValidationError(_('The selected pre-authorization has no remaining amount to charge.'))
            payment_method = helpers._payment_method_for_terminal(preauth.terminal_id)
            if not payment_method:
                raise ValidationError(_('The terminal used for the selected pre-authorization is no longer configured.'))
            auth_amount = float(Decimal(preauth.amount) / Decimal('100'))
            remaining_auth = float(Decimal(helpers.get_preauth_remaining_cents(preauth)) / Decimal('100'))
            remaining = helpers.get_remaining_due_amount(
                sale_order=wizard.sale_order_id,
                invoice=wizard.invoice_id,
            )
            max_charge = min(remaining_auth, remaining) if wizard.currency_id.compare_amounts(remaining, 0) > 0 else 0.0
            wizard.payment_method_id = payment_method
            wizard.amount_authorized = auth_amount
            wizard.amount_due = remaining
            wizard.amount_max = max_charge
            wizard.amount = max_charge

    @api.depends('state', 'transaction_id', 'operation')
    def _compute_payment_page_html(self):
        for wizard in self:
            wizard.payment_page_html = False
            if wizard.state == 'processing' and wizard.transaction_id and wizard.operation:
                page_type = 'void' if wizard.operation == 'void' else 'charge'
                page_url = '/pos_clover/terminal_payment_page/%s/%s' % (page_type, wizard.id)
                wizard.payment_page_html = (
                    '<div class="clover-terminal-iframe-wrap">'
                    '<iframe class="clover-terminal-iframe" src="%s" '
                    'allow="payment *"></iframe></div>' % page_url
                )

    def _validate_selected_preauth(self):
        self.ensure_one()
        helpers = self.env['clover.document.payment']
        preauth = self.preauth_request_id
        if not preauth:
            raise ValidationError(_('Please select an open pre-authorization for this customer.'))
        if preauth.partner_id and self.partner_id and preauth.partner_id != self.partner_id:
            raise ValidationError(_('Please select an open pre-authorization for this customer.'))
        if preauth.status not in helpers._OPEN_PREAUTH_STATUSES:
            raise ValidationError(_('The selected pre-authorization is no longer available.'))
        if helpers.get_preauth_remaining_cents(preauth) <= 0:
            raise ValidationError(_('The selected pre-authorization has no remaining amount to charge or void.'))
        if not preauth.uti:
            raise ValidationError(_('The selected pre-authorization is missing its terminal payment reference.'))
        payment_method = helpers._payment_method_for_terminal(preauth.terminal_id)
        if not payment_method:
            raise ValidationError(_('The terminal used for the selected pre-authorization is no longer configured.'))
        if self.payment_method_id != payment_method:
            raise ValidationError(_('Charge and void must use the same terminal as the pre-authorization.'))
        return preauth, payment_method

    def action_start_charge(self):
        self.ensure_one()
        preauth, payment_method = self._validate_selected_preauth()
        helpers = self.env['clover.document.payment']
        remaining = helpers.get_remaining_due_amount(
            sale_order=self.sale_order_id,
            invoice=self.invoice_id,
        )
        auth_amount = float(Decimal(preauth.amount) / Decimal('100'))
        remaining_auth = float(Decimal(helpers.get_preauth_remaining_cents(preauth)) / Decimal('100'))
        max_charge = min(remaining_auth, remaining)

        if self.currency_id.compare_amounts(self.amount, 0) <= 0:
            raise ValidationError(_('Charge amount must be greater than zero.'))
        if self.currency_id.compare_amounts(self.amount, max_charge) > 0:
            raise ValidationError(_(
                'Charge amount cannot exceed the lesser of the remaining authorized amount and the remaining due.'
            ))

        from odoo.addons.pos_neatclover.controllers.main import PosCloverController
        result = PosCloverController().create_payment_request(
            terminal_id=payment_method.neat_clover_terminal_device_code,
            order_id=self.order_id,
            amount=self.amount,
            user_id=self.env.user.id,
            is_document_payment=True,
            request_type='capture',
            parent_transaction_id=preauth.transaction_id,
            partner_id=self.partner_id.id if self.partner_id else None,
        )
        if not result or result.get('status') not in (200, 201):
            if result and result.get('status') == 409:
                raise ValidationError(_(
                    'This pre-authorization cannot be charged right now '
                    '(fully captured, voided, or another operation is in progress).'
                ))
            raise ValidationError(_('Could not create the terminal charge request.'))

        charge_amount = result['data'].get('amount', self.amount)
        self.write({
            'state': 'processing',
            'operation': 'capture',
            'transaction_id': result['data']['transaction_id'],
            'amount': charge_amount,
            'amount_max': max_charge,
            'amount_due': remaining,
            'amount_authorized': auth_amount,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Charge Pre-Authorization'),
            'res_model': 'clover.terminal.charge.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': dict(self.env.context, dialog_size='medium'),
        }

    def action_start_void(self):
        self.ensure_one()
        preauth, payment_method = self._validate_selected_preauth()
        void_amount = float(Decimal(preauth.amount) / Decimal('100'))

        from odoo.addons.pos_neatclover.controllers.main import PosCloverController
        result = PosCloverController().create_payment_request(
            terminal_id=payment_method.neat_clover_terminal_device_code,
            order_id=self.order_id,
            amount=void_amount,
            user_id=self.env.user.id,
            is_document_payment=True,
            request_type='void_preauth',
            parent_transaction_id=preauth.transaction_id,
            partner_id=self.partner_id.id if self.partner_id else None,
        )
        if not result or result.get('status') not in (200, 201):
            if result and result.get('status') == 409:
                raise ValidationError(_(
                    'This pre-authorization cannot be voided right now '
                    '(fully captured, voided, or another operation is in progress).'
                ))
            raise ValidationError(_('Could not create the terminal void request.'))

        self.write({
            'state': 'processing',
            'operation': 'void',
            'transaction_id': result['data']['transaction_id'],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Void Pre-Authorization'),
            'res_model': 'clover.terminal.charge.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': dict(self.env.context, dialog_size='medium'),
        }


    def action_delete_preauth(self):
        """Remove the selected pre-auth from Odoo without voiding on the terminal."""
        self.ensure_one()
        helpers = self.env['clover.document.payment']
        preauth = self.preauth_request_id
        if not preauth:
            raise ValidationError(_('Please select a pre-authorization.'))
        if preauth.partner_id and self.partner_id and preauth.partner_id != self.partner_id:
            raise ValidationError(_('Please select an open pre-authorization for this customer.'))
        if preauth.status not in helpers._OPEN_PREAUTH_STATUSES:
            raise ValidationError(_('Please select an open pre-authorization for this customer.'))
        if not preauth.mark_as_deleted():
            raise ValidationError(_('This pre-authorization can no longer be deleted.'))
        helpers._post_delete_preauth_note(preauth)
        return {'type': 'ir.actions.act_window_close'}

    def action_mark_done(self):
        self.write({'state': 'done'})
        return {'type': 'ir.actions.act_window_close'}
