package org.example.ticket.payment.gateway;

import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.payment.dto.*;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

public class PortOnePaymentGatewayClient implements PaymentGatewayClient {
    private static final String PAID = "PAID";
    private final RestClient restClient;

    public PortOnePaymentGatewayClient(RestClient restClient) {
        this.restClient = restClient;
    }

    @Override public String provider() { return "PORTONE"; }

    @Override
    public String providerPaymentId(PaymentAuthorization authorization) {
        return authorization.merchantOrderId();
    }

    @Override
    public VerifiedPayment verify(PaymentAuthorization authorization, String providerPaymentId) {
        if (!authorization.merchantOrderId().equals(providerPaymentId)) {
            throw mismatch();
        }
        PortOnePayment payment = fetch(providerPaymentId);
        if (!PAID.equals(payment.status()) || payment.transactionId() == null || payment.paidAt() == null) {
            throw rejected();
        }
        if (!authorization.merchantOrderId().equals(payment.id())
                || payment.amount() == null
                || !authorization.amount().equals(payment.amount().total())
                || !authorization.currency().equals(payment.currency())) {
            throw mismatch();
        }
        return new VerifiedPayment(payment.transactionId(), payment.id(), payment.amount().total(),
                payment.currency(), payment.paidAt().toLocalDateTime());
    }

    private PortOnePayment fetch(String providerPaymentId) {
        try {
            PortOnePayment payment = restClient.get().uri("/payments/{paymentId}", providerPaymentId)
                    .retrieve().body(PortOnePayment.class);
            if (payment == null) throw rejected();
            return payment;
        } catch (BusinessException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw rejected();
        }
    }

    private BusinessException mismatch() {
        return new BusinessException(HttpStatus.CONFLICT, "PAYMENT_DETAILS_MISMATCH",
                "결제 승인 정보가 주문과 일치하지 않습니다.");
    }

    private BusinessException rejected() {
        return new BusinessException(HttpStatus.BAD_GATEWAY, "PAYMENT_PROVIDER_REJECTED",
                "결제사 승인 정보를 확인할 수 없습니다.");
    }
}
