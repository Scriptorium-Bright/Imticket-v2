package org.example.ticket.reservation.controller;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.example.ticket.common.response.ApiResponse;
import org.example.ticket.reservation.dto.*;
import org.example.ticket.reservation.service.ReservationBookingService;
import org.example.ticket.reservation.service.ReservationQueryService;
import org.example.ticket.security.AuthenticatedMember;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/reservations")
@RequiredArgsConstructor
public class ReservationController {
    private final ReservationBookingService bookingService;
    private final ReservationQueryService queryService;

    @PostMapping("/pre-reserve")
    public ApiResponse<ReservationResponse> preReserve(
            @AuthenticationPrincipal AuthenticatedMember member,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @Valid @RequestBody ReservationRequest request
    ) {
        return ApiResponse.success(bookingService.preReserve(member.memberId(), idempotencyKey, request));
    }

    @GetMapping("/{reservationId}")
    public ApiResponse<ReservationDetailResponse> detail(
            @AuthenticationPrincipal AuthenticatedMember member,
            @PathVariable Long reservationId
    ) {
        return ApiResponse.success(queryService.find(member.memberId(), reservationId));
    }
}
