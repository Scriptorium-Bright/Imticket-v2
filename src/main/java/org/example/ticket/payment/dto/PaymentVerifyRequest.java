package org.example.ticket.payment.dto;
import jakarta.validation.constraints.NotBlank;
public record PaymentVerifyRequest(@NotBlank String providerPaymentId) {}
