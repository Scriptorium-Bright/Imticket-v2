package org.example.ticket.payment.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.example.ticket.common.response.ApiResponse;
import org.example.ticket.payment.dto.*;
import org.example.ticket.payment.service.*;
import org.example.ticket.security.AuthenticatedMember;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/payments")
@RequiredArgsConstructor
public class PaymentController {
    private final PaymentPreparationService preparationService;
    private final PaymentVerificationService verificationService;

    @PostMapping("/prepare")
    public ApiResponse<PaymentPrepareResponse> prepare(
            @AuthenticationPrincipal AuthenticatedMember member,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @Valid @RequestBody PaymentPrepareRequest request
    ) {
        return ApiResponse.success(preparationService.prepare(member.memberId(), idempotencyKey, request));
    }

    @PostMapping("/{paymentOrderId}/verify")
    public ApiResponse<PaymentVerificationResponse> verify(
            @AuthenticationPrincipal AuthenticatedMember member,
            @PathVariable Long paymentOrderId,
            @Valid @RequestBody PaymentVerifyRequest request
    ) {
        return ApiResponse.success(verificationService.verify(member.memberId(), paymentOrderId, request));
    }
}
