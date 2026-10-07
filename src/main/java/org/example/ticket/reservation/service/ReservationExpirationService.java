package org.example.ticket.reservation.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.reservation.domain.*;
import org.example.ticket.reservation.repository.*;
import org.example.ticket.seat.domain.Seat;
import org.example.ticket.seat.repository.SeatRepository;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class ReservationExpirationService {
    private final ReservationRepository reservationRepository;
    private final ReservedSeatRepository reservedSeatRepository;
    private final SeatRepository seatRepository;

    @Transactional
    public int expire(LocalDateTime now, int batchSize) {
        List<Long> ids = reservationRepository.findExpiredIds(
                ReservationStatus.PENDING_PAYMENT, now, PageRequest.of(0, batchSize));
        if (ids.isEmpty()) return 0;

        List<Reservation> reservations = reservationRepository.findByIdsForUpdate(ids);
        List<Reservation> expired = reservations.stream()
                .filter(r -> r.getStatus() == ReservationStatus.PENDING_PAYMENT)
                .filter(r -> r.getExpiredAt() != null && r.getExpiredAt().isBefore(now))
                .toList();
        if (expired.isEmpty()) return 0;

        List<Long> expiredIds = expired.stream().map(Reservation::getId).toList();
        List<Long> seatIds = reservedSeatRepository.findSeatIdsByReservationIds(expiredIds);
        List<Seat> seats = seatIds.isEmpty() ? List.of() : seatRepository.findByIdsForUpdate(seatIds);

        expired.forEach(Reservation::expire);
        seats.forEach(Seat::release);
        return expired.size();
    }
}
