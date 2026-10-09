# -*- coding: utf-8 -*-
from decimal import Decimal

from odoo import _, models
from odoo.exceptions import ValidationError


class CloverDocumentPayment(models.AbstractModel):
    _name = 'clover.document.payment'
    _description = 'Clover Sales/Invoice Terminal Payment Helpers'

    _TERMINAL_DONE_STATUSES = ('done', 'refunded', 'resent_done', 'resent_refunded')
    _TERMINAL_FAILED_STATUSES = ('failed', 'cancelled')
    _PREAUTH_SUCCESS_STATUSES = ('authorized',)
    _VOID_SUCCESS_STATUSES = ('voided',)
    _OPEN_PREAUTH_STATUSES = ('authorized', 'processed_authorized')
    _CAPTURE_SUCCESS_STATUSES = (
        'done', 'resent_done', 'processed_done', 'processed_resent_done',
    )
    _PROVIDER_CODE = 'neatcloverpos'
    _PAYMENT_REQUEST_TYPES = ('sale', 'capture')

    def assert_document_fully_paid_for_refund(self, sale_order=None, invoice=None):
        """Backend refunds only once the document is fully paid; otherwise use the portal."""
        msg = _(
            'This document is not fully paid. '
            'Refunds for partially paid sales orders or invoices should be done through the portal. '
            'A credit note for the refund has to be created manually.'
        )
        if invoice:
            if invoice.currency_id.compare_amounts(invoice.amount_residual, 0) > 0:
                raise ValidationError(msg)
            return
        if not sale_order:
            return
        currency = sale_order.currency_id
        invoices = sale_order.invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
        )
        if invoices:
            if any(currency.compare_amounts(inv.amount_residual, 0) > 0 for inv in invoices):
                raise ValidationError(msg)
            return
        order_id = self.order_id_for_sale_order(sale_order)
        paid = float(Decimal(self.get_terminal_paid_cents(order_id)) / Decimal('100'))
        if currency.compare_amounts(paid, sale_order.amount_total) < 0:
            raise ValidationError(msg)


    def assert_document_not_fully_paid_for_payment(self, sale_order=None, invoice=None):
        """Block Pay by... when the document is already fully paid (inverse of refund assert)."""
        msg = _('This document is already fully paid.')
        if invoice:
            if invoice.currency_id.compare_amounts(invoice.amount_residual, 0) <= 0:
                raise ValidationError(msg)
            return
        if not sale_order:
            return
        currency = sale_order.currency_id
        invoices = sale_order.invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
        )
        if invoices:
            if all(currency.compare_amounts(inv.amount_residual, 0) <= 0 for inv in invoices):
                raise ValidationError(msg)
            return
        order_id = self.order_id_for_sale_order(sale_order)
        paid = float(Decimal(self.get_terminal_paid_cents(order_id)) / Decimal('100'))
        if currency.compare_amounts(paid, sale_order.amount_total) >= 0:
            raise ValidationError(msg)

    @staticmethod
    def ensure_single_document(records, document_label):
        if not records:
            raise ValidationError(_('No record selected.'))
        if len(records) != 1:
            raise ValidationError(_('Please select exactly one %s.') % document_label)
        return records[0]

    @staticmethod
    def order_id_for_sale_order(sale_order):
        return 'so-%s' % sale_order.id

    @staticmethod
    def order_id_for_invoice(invoice):
        return 'inv-%s' % invoice.id

    @staticmethod
    def parse_document_order_id(order_id):
        if not order_id:
            return False, False
        if order_id.startswith('so-'):
            try:
                return 'sale.order', int(order_id[3:])
            except ValueError:
                return False, False
        if order_id.startswith('inv-'):
            try:
                return 'account.move', int(order_id[4:])
            except ValueError:
                return False, False
        return False, False

    @staticmethod
    def is_quotation_or_invoice_request(payment_request):
        if not payment_request.is_document_payment:
            return False
        model_name, _record_id = CloverDocumentPayment.parse_document_order_id(payment_request.order_id)
        return bool(model_name)

    def get_terminal_paid_cents(self, order_id):
        requests = self.env['neat.clover.payment.request'].sudo().search([
            ('order_id', '=', order_id),
            ('is_document_payment', '=', True),
            ('request_type', 'in', self._PAYMENT_REQUEST_TYPES),
            ('status', 'like', 'processed_done'),
            ('amount', '>', 0),
        ])
        return sum(max(payment.amount - payment.refunded_amt, 0) for payment in requests)

    def get_preauth_captured_cents(self, preauth_request):
        """Sum of successful capture amounts against a pre-authorization."""
        if not preauth_request:
            return 0
        return preauth_request.get_captured_cents()

    def get_preauth_remaining_cents(self, preauth_request):
        if not preauth_request:
            return 0
        return preauth_request.get_remaining_cents()

    def get_open_preauth_requests(self, order_id=None, partner_id=None):
        """Open pre-auths for a customer (preferred) or legacy document order_id."""
        PaymentRequest = self.env['neat.clover.payment.request'].sudo()
        if partner_id:
            return PaymentRequest.get_open_preauth_requests(partner_id=partner_id)
        if not order_id:
            return PaymentRequest.browse()
        # Legacy fallback for older preauths created before partner scoping.
        candidates = PaymentRequest.search([
            ('order_id', '=', order_id),
            ('is_document_payment', '=', True),
            ('request_type', '=', 'preauth'),
            ('status', 'in', self._OPEN_PREAUTH_STATUSES),
            ('amount', '>', 0),
        ], order='start_date asc')
        return candidates.filtered(lambda r: r.get_remaining_cents() > 0)

    def get_open_preauth_cents(self, order_id=None, partner_id=None):
        return sum(
            r.get_remaining_cents()
            for r in self.get_open_preauth_requests(order_id=order_id, partner_id=partner_id)
        )

    def get_document_partner(self, sale_order=None, invoice=None):
        if sale_order:
            return sale_order.partner_id
        if invoice:
            return invoice.partner_id
        return self.env['res.partner']

    def get_remaining_due_amount(self, sale_order=None, invoice=None):
        if invoice:
            return invoice.amount_residual
        if not sale_order:
            return 0.0
        paid_cents = self.get_terminal_paid_cents(self.order_id_for_sale_order(sale_order))
        paid_amount = float(Decimal(paid_cents) / Decimal('100'))
        invoices = sale_order.invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
        )
        if invoices:
            return sum(invoices.mapped('amount_residual'))
        return sale_order.amount_total - paid_amount

    def get_preauth_available_amount(self, sale_order=None, invoice=None, order_id=None):
        remaining = self.get_remaining_due_amount(sale_order=sale_order, invoice=invoice)
        partner = self.get_document_partner(sale_order=sale_order, invoice=invoice)
        open_preauth = float(Decimal(self.get_open_preauth_cents(partner_id=partner.id if partner else None)) / Decimal('100'))
        return remaining - open_preauth

    def get_terminal_refundable_amount(self, order_id, terminal_id=None):
        domain = [
            ('order_id', '=', order_id),
            ('is_document_payment', '=', True),
            ('request_type', 'in', self._PAYMENT_REQUEST_TYPES),
            ('status', 'like', 'processed_done'),
            ('amount', '>', 0),
        ]
        if terminal_id:
            domain.append(('terminal_id', '=', terminal_id))
        paid_requests = self.env['neat.clover.payment.request'].sudo().search(domain)
        cents = sum(max(payment.amount - payment.refunded_amt, 0) for payment in paid_requests)
        return float(Decimal(cents) / Decimal('100'))

    def get_refund_payment_method_for_order(self, order_id):
        paid_requests = self.env['neat.clover.payment.request'].sudo().search([
            ('order_id', '=', order_id),
            ('is_document_payment', '=', True),
            ('request_type', 'in', self._PAYMENT_REQUEST_TYPES),
            ('status', 'like', 'processed_done'),
            ('amount', '>', 0),
        ], order='start_date asc')
        for paid_request in paid_requests:
            if self.get_terminal_refundable_amount(order_id, paid_request.terminal_id) > 0:
                payment_method = self._payment_method_for_terminal(paid_request.terminal_id)
                if payment_method:
                    return payment_method
        return False

    def get_locked_payment_method_for_order(self, order_id):
        # Lock after first real payment, otherwise after first open/completed preauth.
        paid_requests = self.env['neat.clover.payment.request'].sudo().search([
            ('order_id', '=', order_id),
            ('is_document_payment', '=', True),
            ('request_type', 'in', self._PAYMENT_REQUEST_TYPES),
            ('status', 'like', 'processed_done'),
            ('amount', '>', 0),
        ], order='start_date asc')
        for paid_request in paid_requests:
            if paid_request.amount - paid_request.refunded_amt > 0:
                return self._payment_method_for_terminal(paid_request.terminal_id)
        preauth_requests = self.env['neat.clover.payment.request'].sudo().search([
            ('order_id', '=', order_id),
            ('is_document_payment', '=', True),
            ('request_type', '=', 'preauth'),
            ('status', 'in', self._OPEN_PREAUTH_STATUSES + ('processed_captured', 'processed_voided')),
            ('amount', '>', 0),
        ], order='start_date asc', limit=1)
        if preauth_requests:
            return self._payment_method_for_terminal(preauth_requests.terminal_id)
        return False

    def commit_refunds_for_order_id(self, order_id, terminal_id=None):
        domain = [
            ('status', 'like', 'processed_%'),
            ('order_id', '=', order_id),
            ('amount', '>=', 0),
        ]
        if terminal_id:
            domain.append(('terminal_id', '=', terminal_id))
        paid_payments = self.env['neat.clover.payment.request'].sudo().search(domain)
        for payment in paid_payments:
            payment.write({'refunded_amt': payment.uncommited_refunded_amt})

    def _payment_method_for_terminal(self, terminal_id):
        return self.env['pos.payment.method'].sudo().search([
            ('neat_clover_terminal_device_code', '=', terminal_id),
        ], limit=1)

    def _user_name_for_payment_request(self, payment_request):
        user = self.env['res.users'].browse(payment_request.user_id).exists()
        return user.name if user else _('System')

    def _get_pos_provider(self):
        return self.env['payment.provider'].sudo().search([
            ('code', '=', self._PROVIDER_CODE),
        ], limit=1)

    def _get_pos_payment_method_record(self):
        return self.env['payment.method'].sudo().search([
            ('code', '=', self._PROVIDER_CODE),
        ], limit=1)

    def create_document_payment_transaction(self, order_id, amount_cents):
        """Create a native payment.transaction for an SO/invoice terminal payment.

        Returns the payment.transaction reference (e.g. SO2384098-1).
        Refunds (negative amounts) are not supported here.
        """
        if amount_cents <= 0:
            raise ValidationError(_('Only positive document payments create a payment transaction.'))

        model_name, record_id = self.parse_document_order_id(order_id)
        if not model_name:
            raise ValidationError(_('Invalid document payment order id.'))

        provider = self._get_pos_provider()
        if not provider:
            raise ValidationError(_('Clover Sales POS payment provider is not configured.'))

        amount = float(Decimal(amount_cents) / Decimal('100'))
        # Odoo 16 has no payment.method / payment_method_id on payment.transaction.
        vals = {
            'provider_id': provider.id,
            'amount': amount,
            'operation': 'online_direct',
        }

        if model_name == 'sale.order':
            order = self.env['sale.order'].browse(record_id).exists()
            if not order:
                raise ValidationError(_('Sales order not found.'))
            vals.update({
                'currency_id': order.currency_id.id,
                'partner_id': order.partner_id.id,
                'sale_order_ids': [(6, 0, [order.id])],
            })
        else:
            invoice = self.env['account.move'].browse(record_id).exists()
            if not invoice:
                raise ValidationError(_('Invoice not found.'))
            vals.update({
                'currency_id': invoice.currency_id.id,
                'partner_id': invoice.partner_id.id,
                'invoice_ids': [(6, 0, [invoice.id])],
            })

        tx = self.env['payment.transaction'].sudo().create(vals)
        return tx.reference

    def _ensure_provider_journal(self, provider, pos_payment_method):
        """Use the POS terminal journal and ensure its payment method line exists."""
        journal = pos_payment_method.journal_id if pos_payment_method else False
        if not journal:
            return
        provider = provider.sudo()
        # account.payment.method with code=neatcloverpos is required by Odoo to create
        # the journal payment method line used when posting the account.payment.
        if hasattr(provider, '_setup_payment_method'):
            provider._setup_payment_method(self._PROVIDER_CODE)
        else:
            method_model = self.env['account.payment.method'].sudo()
            if not method_model.search([('code', '=', self._PROVIDER_CODE)], limit=1):
                method_model.create({
                    'name': 'Clover Sales POS',
                    'code': self._PROVIDER_CODE,
                    'payment_type': 'inbound',
                })
        write_vals = {}
        if provider.journal_id != journal:
            write_vals['journal_id'] = journal.id
        if provider.state == 'disabled':
            write_vals['state'] = 'test'
        if write_vals:
            provider.write(write_vals)
        # Always ensure the line exists (inverse may no-op if journal was already set).
        if hasattr(provider, '_ensure_payment_method_line'):
            provider._ensure_payment_method_line()

    def _run_tx_post_process(self, tx):
        # Default finalize disabled — use register payment flow instead.
        # tx._post_process()
        return True

    def _register_document_payments(self, invoices, amount=None, provider=None):
        """Create payments via account.payment.register (same path as payment links)."""
        if not invoices:
            return self.env['account.payment']
        wizard_ctx = {
            'active_model': 'account.move',
            'active_ids': invoices.ids,
            'active_id': invoices.ids[0],
        }
        register_vals = {}
        if provider and provider.journal_id:
            register_vals['journal_id'] = provider.journal_id.id
        if amount is not None:
            register_vals['amount'] = amount
            register_vals['group_payment'] = False
        else:
            register_vals['group_payment'] = True
        wizard = self.env['account.payment.register'].sudo().with_context(**wizard_ctx).create(register_vals)
        return wizard._create_payments()

    def _complete_sale_orders_via_register(self, tx):
        """Confirm/invoice SOs if needed, then register the charged amount onto unpaid invoices."""
        for order in tx.sale_order_ids.filtered(lambda o: o.state in ('draft', 'sent')):
            order.with_context(send_email=True).action_confirm()

        orders = tx.sale_order_ids.filtered(lambda o: o.state == 'sale')
        if not orders:
            tx.is_post_processed = True
            return True

        # Further partials: pay residual on existing invoices (same as invoice flow).
        unpaid = orders.mapped('invoice_ids').filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice' and m.payment_state != 'paid'
        )
        if not unpaid:
            orders._force_lines_to_invoice_policy_order()
            invoices = orders.with_context(raise_if_nothing_to_invoice=False)._create_invoices(final=True)
            draft_invoices = invoices.filtered(lambda m: m.state == 'draft')
            if draft_invoices:
                draft_invoices.action_post()
            unpaid = invoices.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')

        if unpaid:
            payments = self._register_document_payments(unpaid, amount=tx.amount, provider=tx.provider_id)
            if payments and not tx.payment_id:
                tx.payment_id = payments[:1].id
            tx.invoice_ids = [(6, 0, unpaid.ids)]

        tx.is_post_processed = True
        return True

    def _complete_invoices_via_register(self, tx):
        unpaid = tx.invoice_ids.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')
        if unpaid:
            payments = self._register_document_payments(unpaid, amount=tx.amount, provider=tx.provider_id)
            if payments and not tx.payment_id:
                tx.payment_id = payments[:1].id
        tx.is_post_processed = True
        return True

    def _complete_payment_transaction(self, payment_request, pos_payment_method=None):
        tx = self.env['payment.transaction'].sudo().search([
            ('reference', '=', payment_request.transaction_id),
            ('provider_code', '=', self._PROVIDER_CODE),
        ], limit=1)
        if not tx:
            return False
        self._ensure_provider_journal(tx.provider_id, pos_payment_method)
        if tx.state != 'done':
            tx._set_done()
        if tx.is_post_processed:
            return True
        # Finalize disabled — always use register payment flow.
        # self._run_tx_post_process(tx)
        if tx.sale_order_ids:
            return self._complete_sale_orders_via_register(tx)
        if tx.invoice_ids:
            return self._complete_invoices_via_register(tx)
        return False


    def _mark_wizards_done(self, transaction_id):
        for model_name in (
            'clover.terminal.payment.wizard',
            'clover.terminal.refund.wizard',
            'clover.terminal.preauth.wizard',
            'clover.terminal.charge.wizard',
        ):
            if model_name not in self.env:
                continue
            wizards = self.env[model_name].sudo().search([
                ('transaction_id', '=', transaction_id),
                ('state', '=', 'processing'),
            ])
            if wizards:
                wizards.write({'state': 'done'})

    def _post_preauth_note(self, payment_request):
        order_id = payment_request.order_id
        model_name, record_id = self.parse_document_order_id(order_id)
        if not model_name:
            return
        record = self.env[model_name].browse(record_id).exists()
        if not record:
            return
        user_name = self._user_name_for_payment_request(payment_request)
        amount = float(Decimal(payment_request.amount) / Decimal('100'))
        currency = record.currency_id.name
        amount_str = f'{amount:.2f} {currency}'
        body = _(
            'Clover pre-authorization of %(amount)s completed by %(user)s. '
            'Transaction: %(transaction)s. This is not a payment.'
        ) % {
            'amount': amount_str,
            'user': user_name,
            'transaction': payment_request.transaction_id,
        }
        record.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')

    def _post_void_preauth_note(self, payment_request, parent_request):
        order_id = payment_request.order_id
        model_name, record_id = self.parse_document_order_id(order_id)
        if not model_name:
            return
        record = self.env[model_name].browse(record_id).exists()
        if not record:
            return
        user_name = self._user_name_for_payment_request(payment_request)
        amount = float(Decimal(parent_request.amount) / Decimal('100'))
        currency = record.currency_id.name
        amount_str = f'{amount:.2f} {currency}'
        body = _(
            'Clover pre-authorization of %(amount)s voided by %(user)s. '
            'Original transaction: %(transaction)s.'
        ) % {
            'amount': amount_str,
            'user': user_name,
            'transaction': parent_request.transaction_id,
        }
        record.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')


    def _post_delete_preauth_note(self, preauth_request):
        order_id = preauth_request.order_id
        model_name, record_id = self.parse_document_order_id(order_id)
        if not model_name:
            return
        record = self.env[model_name].browse(record_id).exists()
        if not record:
            return
        user_name = self.env.user.name
        amount = float(Decimal(preauth_request.amount) / Decimal('100'))
        currency = record.currency_id.name
        amount_str = f'{amount:.2f} {currency}'
        label = preauth_request.preauth_label or preauth_request.transaction_id
        body = _(
            'Clover pre-authorization of %(amount)s deleted by %(user)s '
            '(not voided on the terminal). Reference: %(label)s. '
            'Transaction: %(transaction)s.'
        ) % {
            'amount': amount_str,
            'user': user_name,
            'label': label,
            'transaction': preauth_request.transaction_id,
        }
        record.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')


    def _post_capture_note(self, payment_request, parent_request=None):
        """Log charge details on the document being charged (may differ from the auth document)."""
        order_id = payment_request.order_id
        model_name, record_id = self.parse_document_order_id(order_id)
        if not model_name:
            return
        record = self.env[model_name].browse(record_id).exists()
        if not record:
            return
        parent = parent_request or payment_request.parent_request_id
        user_name = self._user_name_for_payment_request(payment_request)
        amount = float(Decimal(payment_request.amount) / Decimal('100'))
        currency = record.currency_id.name
        amount_str = f'{amount:.2f} {currency}'
        preauth_tx = parent.transaction_id if parent else payment_request.transaction_id
        preauth_label = (parent.preauth_label if parent else False) or preauth_tx
        body = _(
            'Clover pre-authorization charge of %(amount)s completed by %(user)s. '
            'Charge transaction: %(charge_tx)s. '
            'Pre-authorization: %(preauth_label)s (%(preauth_tx)s).'
        ) % {
            'amount': amount_str,
            'user': user_name,
            'charge_tx': payment_request.transaction_id,
            'preauth_label': preauth_label,
            'preauth_tx': preauth_tx,
        }
        record.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')

    def finalize_document_payment_request(self, payment_request, payment_method=None):
        payment_request.ensure_one()
        if not self.is_quotation_or_invoice_request(payment_request):
            return False
        if payment_request.status.startswith('processed_'):
            return True

        request_type = payment_request.request_type or 'sale'

        if request_type == 'preauth':
            if payment_request.status not in self._PREAUTH_SUCCESS_STATUSES:
                return False
            self._post_preauth_note(payment_request)
            payment_request.write({'status': 'processed_authorized'})
            self._mark_wizards_done(payment_request.transaction_id)
            return True

        if request_type == 'void_preauth':
            if payment_request.status not in self._VOID_SUCCESS_STATUSES:
                return False
            parent = payment_request.parent_request_id
            if parent and parent.status in self._OPEN_PREAUTH_STATUSES:
                parent.write({'status': 'processed_voided'})
            self._post_void_preauth_note(payment_request, parent or payment_request)
            payment_request.write({'status': 'processed_voided'})
            self._mark_wizards_done(payment_request.transaction_id)
            return True

        if payment_request.status not in self._TERMINAL_DONE_STATUSES:
            return False

        if payment_request.amount < 0:
            self.commit_refunds_for_order_id(
                payment_request.order_id,
                terminal_id=payment_request.terminal_id,
            )

        if not payment_method:
            payment_method = self._payment_method_for_terminal(payment_request.terminal_id)

        self.register_document_payment(payment_request, payment_method)
        if request_type == 'capture':
            parent = payment_request.parent_request_id
            # Always log charge refs on the document being charged.
            self._post_capture_note(payment_request, parent)
            if parent and parent.status in self._OPEN_PREAUTH_STATUSES:
                # One capture per preauth for now: close after first successful charge.
                # Remaining-amount helpers stay available if we re-enable multi-capture later.
                parent.write({'status': 'processed_captured'})
        payment_request.write({'status': 'processed_' + payment_request.status})
        self._mark_wizards_done(payment_request.transaction_id)
        return True

    def get_document_payment_status(self, payment_request):
        payment_request.ensure_one()
        is_refund = payment_request.amount < 0
        request_type = payment_request.request_type or 'sale'
        if payment_request.status.startswith('processed_'):
            if request_type == 'preauth':
                message = _('Pre-authorization completed successfully. This is not a payment. You may close this window.')
            elif request_type == 'void_preauth':
                message = _('Pre-authorization voided successfully. You may close this window.')
            elif is_refund:
                message = _('Refund completed successfully. A credit note for the refund has to be created manually. You may close this window.')
            elif request_type == 'capture':
                message = _('Charge completed successfully. You may close this window.')
            else:
                message = _('Payment completed successfully. You may close this window.')
            return {
                'state': 'done',
                'success': True,
                'is_refund': is_refund,
                'request_type': request_type,
                'message': message,
            }
        if payment_request.status in self._TERMINAL_FAILED_STATUSES:
            if request_type == 'preauth':
                message = _('The pre-authorization was declined or cancelled on the terminal.')
            elif request_type == 'void_preauth':
                message = _('The void was declined or cancelled on the terminal.')
            elif request_type == 'capture':
                message = _('The charge was declined or cancelled on the terminal.')
            elif is_refund:
                message = _('The refund was declined or cancelled on the terminal.')
            else:
                message = _('The payment was declined or cancelled on the terminal.')
            return {
                'state': 'error',
                'success': False,
                'is_refund': is_refund,
                'request_type': request_type,
                'message': message,
            }
        if (
            payment_request.status in self._TERMINAL_DONE_STATUSES
            or payment_request.status in self._PREAUTH_SUCCESS_STATUSES
            or payment_request.status in self._VOID_SUCCESS_STATUSES
        ):
            return {
                'state': 'processing',
                'success': False,
                'is_refund': is_refund,
                'request_type': request_type,
                'message': _('Confirming with Odoo...'),
            }
        if request_type == 'preauth':
            message = _('Waiting for pre-authorization on the Clover terminal...')
        elif request_type == 'void_preauth':
            message = _('Waiting for void on the Clover terminal...')
        elif request_type == 'capture':
            message = _('Waiting for charge on the Clover terminal...')
        elif is_refund:
            message = _('Waiting for the refund on the Clover terminal...')
        else:
            message = _('Waiting for payment on the Clover terminal...')
        return {
            'state': 'waiting',
            'success': False,
            'is_refund': is_refund,
            'request_type': request_type,
            'message': message,
        }

    def register_document_payment(self, payment_request, payment_method):
        order_id = payment_request.order_id
        model_name, record_id = self.parse_document_order_id(order_id)
        if not model_name:
            return

        user_name = self._user_name_for_payment_request(payment_request)
        amount = float(Decimal(payment_request.amount) / Decimal('100'))
        if model_name == 'sale.order':
            order = self.env['sale.order'].browse(record_id).exists()
            if not order:
                return
            if amount > 0:
                self._complete_payment_transaction(payment_request, payment_method)
            elif amount < 0:
                self._register_sale_order_refund(order, abs(amount), payment_request.transaction_id, user_name)
        elif model_name == 'account.move':
            invoice = self.env['account.move'].browse(record_id).exists()
            if not invoice:
                return
            if amount > 0:
                self._complete_payment_transaction(payment_request, payment_method)
            elif amount < 0:
                self._register_invoice_refund(invoice, abs(amount), payment_request.transaction_id, user_name)

    def _register_sale_order_refund(self, order, amount, transaction_id, user_name):
        amount_str = f'{amount:.2f} {order.currency_id.name}'
        order.message_post(
            body=_(
                'Clover refund of %(amount)s completed by %(user)s.'
                ' Transaction: %(transaction)s.'
                ' A credit note for the refund has to be created manually.'
            ) % {
                'amount': amount_str,
                'user': user_name,
                'transaction': transaction_id,
            },
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    def _register_invoice_refund(self, invoice, amount, transaction_id, user_name):
        amount_str = f'{amount:.2f} {invoice.currency_id.name}'
        invoice.message_post(
            body=_(
                'Clover refund of %(amount)s completed by %(user)s.'
                ' Transaction: %(transaction)s.'
                ' A credit note for the refund has to be created manually.'
            ) % {
                'amount': amount_str,
                'user': user_name,
                'transaction': transaction_id,
            },
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    @staticmethod
    def check_sale_order(order):
        order.env['clover.document.payment'].assert_document_not_fully_paid_for_payment(
            sale_order=order,
        )

    @staticmethod
    def check_invoice(invoice):
        if not invoice.is_invoice(include_receipts=False) or invoice.state != 'posted':
            raise ValidationError(_('Clover terminal payment is only available for posted customer invoices.'))
        invoice.env['clover.document.payment'].assert_document_not_fully_paid_for_payment(
            invoice=invoice,
        )
