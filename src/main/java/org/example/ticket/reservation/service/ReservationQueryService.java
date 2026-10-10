package org.example.ticket.reservation.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.common.exception.BusinessException;
import org.example.ticket.reservation.domain.Reservation;
import org.example.ticket.reservation.dto.ReservationDetailResponse;
import org.example.ticket.reservation.repository.ReservationRepository;
import org.example.ticket.reservation.repository.ReservedSeatRepository;
import org.example.ticket.seat.dto.SeatResponse;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@RequiredArgsConstructor
public class ReservationQueryService {

    private final ReservationRepository reservationRepository;
    private final ReservedSeatRepository reservedSeatRepository;

    @Transactional(readOnly = true)
    public ReservationDetailResponse find(Long memberId, Long reservationId) {
        Reservation reservation = reservationRepository.findById(reservationId)
                .orElseThrow(() -> new BusinessException(HttpStatus.NOT_FOUND,
                        "RESERVATION_NOT_FOUND", "예약을 찾을 수 없습니다."));

        if (!reservation.getMember().getId().equals(memberId)) {
            throw new BusinessException(HttpStatus.FORBIDDEN,
                    "RESERVATION_NOT_OWNER", "본인의 예약만 조회할 수 있습니다.");
        }

        List<SeatResponse> seats = reservedSeatRepository.findSeatResponsesByReservationId(reservationId);
        return ReservationDetailResponse.of(reservation, seats);
    }
}
