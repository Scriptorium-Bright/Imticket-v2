package org.example.ticket.payment.gateway;
import org.example.ticket.payment.dto.*;
public interface PaymentGatewayClient {
    String provider();
    String providerPaymentId(PaymentAuthorization authorization);
    VerifiedPayment verify(PaymentAuthorization authorization, String providerPaymentId);
}
