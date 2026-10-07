package org.example.ticket.payment.gateway;

import org.example.ticket.payment.dto.*;
import java.time.LocalDateTime;

public class FakePaymentGatewayClient implements PaymentGatewayClient {
    @Override public String provider() { return "FAKE"; }

    @Override
    public String providerPaymentId(PaymentAuthorization authorization) {
        return "fake-" + authorization.merchantOrderId();
    }

    @Override
    public VerifiedPayment verify(PaymentAuthorization authorization, String providerPaymentId) {
        return new VerifiedPayment(
                providerPaymentId,
                authorization.merchantOrderId(),
                authorization.amount(),
                authorization.currency(),
                LocalDateTime.now()
        );
    }
}
