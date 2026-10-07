package org.example.ticket.reservation.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.reservation.domain.*;
import org.example.ticket.reservation.dto.*;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class ReservationBookingService {
    private static final int LEASE_SECONDS = 30;

    private final ReservationIdempotencyService idempotencyService;
    private final ReservationCreationService creationService;
    private final ReservationRequestHasher hasher;

    public ReservationResponse preReserve(Long memberId, String rawKey, ReservationRequest request) {
        if (rawKey == null || rawKey.isBlank()) {
            throw new BusinessException(HttpStatus.BAD_REQUEST, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key가 필요합니다.");
        }
        String key = rawKey.trim();
        String hash = hasher.hash(request);
        String token = UUID.randomUUID().toString();
        LocalDateTime now = LocalDateTime.now();
        ReservationIdempotency claim;
        try {
            claim = idempotencyService.createClaim(memberId, key, hash, token, now.plusSeconds(LEASE_SECONDS));
        } catch (DataIntegrityViolationException duplicate) {
            return resolveExisting(memberId, key, hash, request);
        }
        return execute(memberId, request, hash, claim.getId(), token);
    }

    private ReservationResponse resolveExisting(Long memberId, String key, String hash, ReservationRequest request) {
        ReservationIdempotency existing = idempotencyService.find(memberId, key)
                .orElseThrow(() -> new BusinessException(HttpStatus.CONFLICT, "IDEMPOTENCY_PROCESSING",
                        "같은 멱등 키 요청을 확인하는 중입니다."));
        if (!existing.getRequestHash().equals(hash)) {
            throw new BusinessException(HttpStatus.CONFLICT, "IDEMPOTENCY_CONFLICT",
                    "같은 멱등 키에 다른 요청 본문을 사용할 수 없습니다.");
        }
        if (existing.getStatus() == IdempotencyStatus.SUCCEEDED) {
            return ReservationResponse.from(existing.getReservation());
        }
        if (existing.getStatus() == IdempotencyStatus.FAILED) {
            throw new BusinessException(HttpStatus.CONFLICT,
                    existing.getLastErrorCode() == null ? "RESERVATION_FAILED" : existing.getLastErrorCode(),
                    "동일 요청이 이전에 실패했습니다.");
        }

        LocalDateTime now = LocalDateTime.now();
        if (existing.getLeaseExpiresAt().isAfter(now)) {
            throw new BusinessException(HttpStatus.CONFLICT, "IDEMPOTENCY_PROCESSING", "동일 요청을 처리 중입니다.");
        }

        String token = UUID.randomUUID().toString();
        if (!idempotencyService.reclaim(existing.getId(), hash, token, now, now.plusSeconds(LEASE_SECONDS))) {
            throw new BusinessException(HttpStatus.CONFLICT, "IDEMPOTENCY_PROCESSING", "동일 요청을 처리 중입니다.");
        }
        return execute(memberId, request, hash, existing.getId(), token);
    }

    private ReservationResponse execute(Long memberId, ReservationRequest request,
                                        String hash, Long claimId, String token) {
        try {
            return creationService.create(memberId, request, hash, claimId, token);
        } catch (BusinessException exception) {
            idempotencyService.failIfOwned(claimId, token, exception.getCode());
            throw exception;
        }
    }
}
