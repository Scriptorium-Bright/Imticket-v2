package org.example.ticket.reservation.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.member.domain.Member;
import org.example.ticket.member.repository.MemberRepository;
import org.example.ticket.reservation.domain.*;
import org.example.ticket.reservation.repository.ReservationIdempotencyRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.Optional;

@Service
@RequiredArgsConstructor
public class ReservationIdempotencyService {
    private final ReservationIdempotencyRepository repository;
    private final MemberRepository memberRepository;

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public ReservationIdempotency createClaim(Long memberId, String key, String hash,
                                               String attemptToken, LocalDateTime leaseUntil) {
        Member member = memberRepository.getReferenceById(memberId);
        return repository.saveAndFlush(ReservationIdempotency.builder()
                .member(member).idempotencyKey(key).requestHash(hash)
                .status(IdempotencyStatus.PROCESSING).attemptToken(attemptToken)
                .leaseExpiresAt(leaseUntil).build());
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW, readOnly = true)
    public Optional<ReservationIdempotency> find(Long memberId, String key) {
        return repository.findByMemberIdAndIdempotencyKey(memberId, key);
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public boolean reclaim(Long id, String hash, String token, LocalDateTime now, LocalDateTime leaseUntil) {
        ReservationIdempotency claim = repository.findByIdForUpdate(id).orElse(null);
        if (claim == null || !claim.getRequestHash().equals(hash)) return false;
        if (claim.getStatus() == IdempotencyStatus.SUCCEEDED || claim.getStatus() == IdempotencyStatus.FAILED) return false;
        if (claim.getLeaseExpiresAt().isAfter(now)) return false;
        claim.reclaim(token, leaseUntil);
        return true;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void failIfOwned(Long id, String token, String errorCode) {
        ReservationIdempotency claim = repository.findByIdForUpdate(id).orElse(null);
        if (claim != null && claim.getStatus() == IdempotencyStatus.PROCESSING
                && claim.getAttemptToken().equals(token)) {
            claim.fail(errorCode);
        }
    }
}
