package org.example.ticket.payment.dto;
public record PaymentAuthorization(Long orderId, Long reservationId, String merchantOrderId,
                                   Integer amount, String currency) {}
