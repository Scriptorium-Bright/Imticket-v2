package org.example.ticket.reservation.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.member.domain.Member;
import org.example.ticket.member.repository.MemberRepository;
import org.example.ticket.reservation.domain.*;
import org.example.ticket.reservation.dto.*;
import org.example.ticket.reservation.repository.*;
import org.example.ticket.seat.domain.*;
import org.example.ticket.seat.repository.SeatRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class ReservationCreationService {
    private final ReservationIdempotencyRepository idempotencyRepository;
    private final ReservationRepository reservationRepository;
    private final MemberRepository memberRepository;
    private final SeatRepository seatRepository;

    @Value("${reservation.hold-duration}")
    private Duration holdDuration;

    @Transactional
    public ReservationResponse create(Long memberId, ReservationRequest request,
                                      String requestHash, Long claimId, String attemptToken) {
        ReservationIdempotency claim = idempotencyRepository.findByIdForUpdate(claimId)
                .orElseThrow(() -> conflict("IDEMPOTENCY_PROCESSING", "예약 요청 소유권을 확인할 수 없습니다."));
        if (!claim.ownedBy(memberId, requestHash, attemptToken)) {
            throw conflict("IDEMPOTENCY_PROCESSING", "다른 요청이 같은 멱등 키를 처리 중입니다.");
        }

        List<Long> seatIds = request.seatIds().stream().distinct().sorted().toList();
        if (seatIds.size() != request.seatIds().size()) {
            throw badRequest("DUPLICATED_SEAT", "중복 좌석이 포함되어 있습니다.");
        }

        List<Seat> seats = seatRepository.findByPerformanceTimeIdAndIdsForUpdate(request.performanceTimeId(), seatIds);
        if (seats.size() != seatIds.size()) {
            throw badRequest("SEAT_NOT_FOUND", "공연 회차에 속하지 않는 좌석이 포함되어 있습니다.");
        }
        if (seats.stream().anyMatch(seat -> seat.getStatus() != SeatStatus.AVAILABLE)) {
            throw conflict("SEAT_UNAVAILABLE", "이미 선점되거나 판매된 좌석이 있습니다.");
        }

        Member member = memberRepository.findById(memberId)
                .orElseThrow(() -> new BusinessException(HttpStatus.UNAUTHORIZED, "MEMBER_NOT_FOUND", "사용자를 찾을 수 없습니다."));

        seats.forEach(Seat::lock);
        Reservation reservation = Reservation.builder()
                .reservationCode(UUID.randomUUID().toString())
                .member(member)
                .totalPrice(seats.stream().mapToInt(Seat::getPrice).sum())
                .status(ReservationStatus.PENDING_PAYMENT)
                .expiredAt(LocalDateTime.now().plus(holdDuration))
                .build();
        reservation.setReservedSeats(seats.stream()
                .map(seat -> ReservedSeat.builder().reservation(reservation).seat(seat).build())
                .toList());
        reservationRepository.save(reservation);
        claim.succeed(reservation);
        return ReservationResponse.from(reservation);
    }

    private BusinessException conflict(String code, String message) {
        return new BusinessException(HttpStatus.CONFLICT, code, message);
    }
    private BusinessException badRequest(String code, String message) {
        return new BusinessException(HttpStatus.BAD_REQUEST, code, message);
    }
}
